from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Tuple
import uvicorn
import sys
import os
import json
import secrets
import time


# 添加父目录到路径以便导入 bot 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="KOOK Bot 管理后台", version="1.0.0")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 登录失败记录
login_attempts: Dict[str, Dict] = {}  # ip -> {count, last_attempt, locked_until}

def check_login_lock(ip: str) -> Tuple[bool, Optional[int]]:
    """检查是否被锁定，返回(是否锁定, 等待秒数)"""
    if ip not in login_attempts:
        return False, None
    
    record = login_attempts[ip]
    if 'locked_until' in record and record['locked_until'] > time.time():
        wait_time = int(record['locked_until'] - time.time())
        return True, wait_time
    
    # 重置计数（如果超过1小时）
    if time.time() - record.get('last_attempt', 0) > 3600:
        login_attempts[ip] = {'count': 0, 'last_attempt': time.time()}
    
    return False, None

def record_failed_login(ip: str):
    """记录登录失败"""
    if ip not in login_attempts:
        login_attempts[ip] = {'count': 0, 'last_attempt': time.time()}
    
    login_attempts[ip]['count'] += 1
    login_attempts[ip]['last_attempt'] = time.time()
    
    count = login_attempts[ip]['count']
    if count >= 3:
        # 锁定时间：60秒、120秒、180秒
        lock_time = 60 * (count - 2)  # 3次60s，4次120s，5次180s
        if lock_time > 180:
            lock_time = 180
        login_attempts[ip]['locked_until'] = time.time() + lock_time

def reset_login_attempts(ip: str):
    """重置登录尝试"""
    if ip in login_attempts:
        del login_attempts[ip]

# 登录请求模型
class LoginRequest(BaseModel):
    password: str

# 设置密码请求模型
class SetPasswordRequest(BaseModel):
    password: str

# 会话存储
sessions: Dict[str, float] = {}  # token -> expiry_time

def create_session_token():
    """创建会话令牌"""
    return secrets.token_urlsafe(32)

def is_valid_token(token: str) -> bool:
    """验证令牌有效性"""
    if token not in sessions:
        return False
    if sessions[token] < time.time():
        del sessions[token]
        return False
    return True

# 数据模型
class TaskInfo(BaseModel):
    show_id: str
    rule_desc: str
    content: str
    channel_id: str
    channel_name: Optional[str] = None
    guild_name: Optional[str] = None
    type: str
    params: dict

class SendMessageRequest(BaseModel):
    channel_id: str
    content: str



class CreateTaskRequest(BaseModel):
    channel_id: str
    task_type: str  # interval, cron
    params: dict
    content: str
    rule_desc: str

# 全局变量，将在 app.py 中导入并赋值
bot_instance = None
scheduler_instance = None
runtime_tasks_dict = None
channel_next_id_dict = None
save_task_config_func = None
safe_send_reminder_func = None
get_channel_info_func = None
get_guild_name_func = None

def init_bot_manager(bot, scheduler, runtime_tasks, channel_next_id, save_func, send_func, get_channel_info, get_guild_name):
    """初始化 bot 管理器的全局变量"""
    global bot_instance, scheduler_instance, runtime_tasks_dict
    global channel_next_id_dict, save_task_config_func, safe_send_reminder_func
    global get_channel_info_func, get_guild_name_func
    bot_instance = bot
    scheduler_instance = scheduler
    runtime_tasks_dict = runtime_tasks
    channel_next_id_dict = channel_next_id
    save_task_config_func = save_func
    safe_send_reminder_func = send_func
    # 频道和服务器信息获取函数
    get_channel_info_func = get_channel_info
    get_guild_name_func = get_guild_name

def get_client_ip(request: Request) -> str:
    """获取客户端 IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# 挂载静态文件（前端）- 必须放在路由之前
app.mount("/static", StaticFiles(directory="static"), name="static")

# API 路由
@app.get("/")
async def root():
    """返回前端页面"""
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")

@app.get("/api")
async def api_root():
    return {"message": "KOOK Bot 管理后台 API", "version": "1.0.0"}

@app.post("/api/login")
async def login(req: LoginRequest, request: Request):
    """用户登录"""
    client_ip = get_client_ip(request)
    
    # 检查是否被锁定
    is_locked, wait_time = check_login_lock(client_ip)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请等待 {wait_time} 秒后再试"
        )
    
    # 获取配置的密码
    config_password = get_config_password()
    
    # 验证密码
    if req.password == config_password:
        # 登录成功，重置尝试记录
        reset_login_attempts(client_ip)
        
        # 创建会话
        token = create_session_token()
        sessions[token] = time.time() + 86400  # 24小时过期
        return {"token": token, "message": "登录成功"}
    
    # 登录失败
    record_failed_login(client_ip)
    is_locked, wait_time = check_login_lock(client_ip)
    
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"密码错误，账户已锁定 {wait_time} 秒"
        )
    else:
        remaining = 3 - login_attempts[client_ip]['count']
        raise HTTPException(
            status_code=401,
            detail=f"密码错误，还有 {remaining} 次尝试机会"
        )

def get_config_password() -> str:
    """获取配置的密码，优先从 tasks.json 读取，否则使用环境变量"""
    # 优先从 tasks.json 读取
    if os.path.exists("tasks.json"):
        try:
            with open("tasks.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("admin_password"):
                    return data["admin_password"]
        except:
            pass
    # 回退到环境变量
    return os.getenv("WEB_ADMIN_PASSWORD", "admin123")

@app.post("/api/setup")
async def setup_password(req: SetPasswordRequest, request: Request):
    """首次设置密码"""
    config_password = get_config_password()
    
    # 检查是否需要设置密码（使用默认密码或环境变量密码时需要设置）
    if config_password != "admin123":
        raise HTTPException(status_code=400, detail="密码已设置，请登录后修改")
    
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="密码长度至少4位")
    
    # 保存密码到 tasks.json
    config_data = {}
    if os.path.exists("tasks.json"):
        try:
            with open("tasks.json", "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except:
            pass
    
    config_data["admin_password"] = req.password
    
    with open("tasks.json", "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)
    
    return {"message": "密码设置成功，请登录"}

@app.get("/api/check-setup")
async def check_setup():
    """检查是否已设置密码"""
    config_password = get_config_password()
    return {"setup": config_password != "admin123"}

@app.get("/api/check-auth")
async def check_auth(request: Request):
    """检查登录状态"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return {"authenticated": False}
    
    token = auth_header.replace('Bearer ', '')
    if not is_valid_token(token):
        return {"authenticated": False}
    
    return {"authenticated": True}

@app.post("/api/logout")
async def logout(request: Request):
    """登出"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header.replace('Bearer ', '')
        if token in sessions:
            del sessions[token]
    return {"message": "登出成功"}

def verify_token(request: Request):
    """验证 token 的依赖函数"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail="请先登录")
    
    token = auth_header.replace('Bearer ', '')
    if not is_valid_token(token):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    
    return True

@app.get("/api/tasks")
async def list_tasks(channel_id: Optional[str] = None, authenticated: bool = Depends(verify_token)):
    """获取所有定时任务（需要登录）"""
    tasks = []
    for inner_id, task in runtime_tasks_dict.items():
        # 获取频道和服务器信息
        channel_info = await get_channel_info_func(task["channel_id"])
        guild_name = await get_guild_name_func(channel_info.get("guild_id"))
        
        task_info = TaskInfo(
            show_id=task["show_id"],
            rule_desc=task["rule_desc"],
            content=task["content"],
            channel_id=task["channel_id"],
            channel_name=channel_info.get("name", "未知频道"),
            guild_name=guild_name,
            type=task["type"],
            params=task["params"]
        )
        if channel_id is None or task["channel_id"] == channel_id:
            tasks.append(task_info)
    return {"tasks": tasks}

@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest, authenticated: bool = Depends(verify_token)):
    """创建新的定时任务（需要登录）"""
    try:
        import asyncio
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger
        
        cid = req.channel_id
        if cid not in channel_next_id_dict:
            channel_next_id_dict[cid] = 1
        num = channel_next_id_dict[cid]
        show_id = f"r{num}"
        inner_id = f"task_{cid}_{show_id}"
        
        # 构建触发器
        trigger = None
        if req.task_type == "interval":
            trigger = IntervalTrigger(**req.params)
        elif req.task_type == "cron":
            trigger = CronTrigger(**req.params, timezone="Asia/Shanghai")
        
        if not trigger:
            raise HTTPException(status_code=400, detail="无效的任务类型")
        
        # 定义发送函数
        async def job_func():
            await safe_send_reminder_func(cid, req.content)
        
        # 检查是否已存在
        if scheduler_instance.get_job(inner_id):
            scheduler_instance.remove_job(inner_id)
            if inner_id in runtime_tasks_dict:
                del runtime_tasks_dict[inner_id]
        
        job = scheduler_instance.add_job(
            job_func, 
            trigger, 
            id=inner_id, 
            misfire_grace_time=300
        )
        
        runtime_tasks_dict[inner_id] = {
            "job": job,
            "show_id": show_id,
            "type": req.task_type,
            "params": req.params,
            "content": req.content,
            "channel_id": cid,
            "rule_desc": req.rule_desc
        }
        
        channel_next_id_dict[cid] += 1
        save_task_config_func()
        
        return {
            "message": f"任务 {show_id} 创建成功",
            "show_id": show_id,
            "rule_desc": req.rule_desc
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")

@app.delete("/api/tasks/{show_id}")
async def delete_task(show_id: str, authenticated: bool = Depends(verify_token)):
    """删除指定任务（需要登录）"""
    target_inner_id = None
    for inner_id, task in runtime_tasks_dict.items():
        if task["show_id"] == show_id:
            target_inner_id = inner_id
            break

    if not target_inner_id:
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        if scheduler_instance.get_job(target_inner_id):
            scheduler_instance.remove_job(target_inner_id)
        del runtime_tasks_dict[target_inner_id]
        save_task_config_func()
        return {"message": f"已删除任务 {show_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

@app.post("/api/send-message")
async def send_message(req: SendMessageRequest, authenticated: bool = Depends(verify_token)):
    """手动发送消息到指定频道（需要登录）"""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        asyncio.ensure_future(safe_send_reminder_func(req.channel_id, req.content), loop=loop)
        return {"message": "消息发送中"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"发送失败: {str(e)}")

@app.get("/api/servers")
async def list_servers(authenticated: bool = Depends(verify_token)):
    """获取机器人所在的所有服务器（需要登录）"""
    try:
        # 获取机器人加入的所有服务器
        guild_list = await bot_instance.client.fetch_guild_list()

        servers_data = []
        for guild in guild_list:
            servers_data.append({
                "id": str(guild.id),
                "name": guild.name
            })

        # 按名称排序
        servers_data.sort(key=lambda x: x['name'])
        return {"servers": servers_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取服务器列表失败: {str(e)}")

@app.get("/api/channels")
async def list_channels(authenticated: bool = Depends(verify_token)):
    """获取机器人所在的所有频道（需要登录）"""
    try:
        # 获取机器人所在的所有服务器和频道
        channels_data = []
        unique_channels = {}

        # 先从任务中获取已有的频道
        for task in runtime_tasks_dict.values():
            cid = task["channel_id"]
            if cid not in unique_channels:
                try:
                    import asyncio
                    async def get_channel_name():
                        try:
                            channel = await bot_instance.client.fetch_public_channel(cid)
                            guild_name = await get_guild_name_func(channel.guild_id)
                            return {
                                "id": cid,
                                "name": channel.name,
                                "guild_id": str(channel.guild_id) if channel.guild_id else None,
                                "guild_name": guild_name
                            }
                        except:
                            return {
                                "id": cid,
                                "name": f"频道 {cid}",
                                "guild_id": None,
                                "guild_name": None
                            }

                    channel_info = await get_channel_name()
                    unique_channels[cid] = channel_info
                except:
                    unique_channels[cid] = {
                        "id": cid,
                        "name": f"频道 {cid}",
                        "guild_id": None,
                        "guild_name": None
                    }

        # 尝试获取机器人加入的所有服务器及其频道
        try:
            import asyncio
            import logging
            logger = logging.getLogger(__name__)
            guild_list = await bot_instance.client.fetch_guild_list()

            async def fetch_channels_for_guild(guild):
                try:
                    guild_detail = await bot_instance.client.fetch_guild(guild.id)

                    if hasattr(guild_detail, 'channels') and guild_detail.channels:
                        for channel in guild_detail.channels:
                            try:
                                if isinstance(channel, dict):
                                    cid = str(channel.get('id'))
                                    name = channel.get('name', '未知频道')
                                    channel_type = channel.get('type', '')
                                else:
                                    cid = str(channel.id)
                                    name = channel.name
                                    channel_type = getattr(channel, 'type', '') if hasattr(channel, 'type') else ''

                                # 只添加文字频道（type=1 是文字频道）
                                if cid and cid not in unique_channels:
                                    is_text = channel_type == 1 or str(channel_type) == '1'
                                    if is_text:
                                        unique_channels[cid] = {
                                            "id": cid,
                                            "name": name,
                                            "guild_id": str(guild.id),
                                            "guild_name": guild.name
                                        }
                            except Exception as e:
                                logger.warning(f"处理频道失败: {e}")
                except Exception as e:
                    logger.error(f"获取服务器 {guild.name} 的频道失败: {e}", exc_info=True)

            # 并发获取所有服务器的频道
            tasks = [fetch_channels_for_guild(guild) for guild in guild_list]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"获取服务器列表失败: {e}")
            pass

        channels_data = list(unique_channels.values())
        channels_data.sort(key=lambda x: (x['guild_name'] or '', x['name']))
        return {"channels": channels_data}
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"获取频道列表异常: {e}")
        # 如果获取频道信息失败，返回基础列表
        unique_channels = list(set(t["channel_id"] for t in runtime_tasks_dict.values()))
        return {"channels": [{"id": cid, "name": f"频道 {cid}", "guild_id": None, "guild_name": None} for cid in unique_channels]}



@app.get("/api/stats")
async def get_stats(authenticated: bool = Depends(verify_token)):
    """获取统计信息（需要登录）"""
    total_tasks = len(runtime_tasks_dict)
    channels = len(set(t["channel_id"] for t in runtime_tasks_dict.values()))
    return {
        "total_tasks": total_tasks,
        "total_channels": channels,
        "bot_status": "running" if bot_instance else "stopped"
    }

def run_api_server(host: str = "0.0.0.0", port: int = 8000):
    """启动 API 服务器"""
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    run_api_server()
