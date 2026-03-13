import os
import re
import json
import logging
import asyncio
from typing import Dict
from tempfile import NamedTemporaryFile
from dotenv import load_dotenv
from khl import Bot, Message
from khl.client import Client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

# 导入 API 服务器
import api_server

# ====================== 配置与初始化 ======================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv('KOOK_BOT_TOKEN')
if not TOKEN:
    raise ValueError("请配置 KOOK_BOT_TOKEN")

bot = Bot(token=TOKEN)
scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')

TASK_FILE = "tasks.json"
TASK_TMP_SUFFIX = ".tmp"

# 运行时状态
runtime_tasks: Dict[str, dict] = {}  # inner_id -> task_info
channel_next_id: Dict[str, int] = {} # channel_id -> next_show_num

# 常量映射
WEEKDAY_MAP = {
    '一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6,
    '1': 0, '2': 1, '3': 2, '4': 3, '5': 4, '6': 5, '7': 6
}
CN_WEEKDAY = ['一', '二', '三', '四', '五', '六', '日']
TIME_PATTERN = re.compile(r'^\d{1,2}:\d{2}$')

# ====================== 核心工具：安全持久化（原子写入） ======================
def safe_save_json(data, filepath):
    """原子性写入JSON：先写临时文件，再重命名，防止崩溃损坏"""
    dir_path = os.path.dirname(filepath) or '.'
    with NamedTemporaryFile('w', dir=dir_path, suffix=TASK_TMP_SUFFIX, 
                            delete=False, encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path = f.name
    
    try:
        # 先尝试使用 os.replace，失败则回退到 copy+remove
        try:
            os.replace(tmp_path, filepath)
        except OSError:
            # 对于跨文件系统的情况，使用 copy+remove 的方式
            import shutil
            shutil.copy2(tmp_path, filepath)
            os.remove(tmp_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise e

def save_task_configs():
    config_data = {
        "channel_next_id": channel_next_id,
        "task_configs": []
    }
    for inner_id, task_info in runtime_tasks.items():
        config_data["task_configs"].append({
            "inner_id": inner_id,
            "show_id": task_info["show_id"],
            "type": task_info["type"],
            "params": task_info["params"],
            "content": task_info["content"],
            "channel_id": task_info["channel_id"],
            "rule_desc": task_info["rule_desc"]
        })
    safe_save_json(config_data, TASK_FILE)

# ====================== 核心工具：KOOK 消息发送（带重试） ======================
async def safe_send_reminder(channel_id: str, text: str, retries: int = 2):
    """安全发送提醒，带重试机制"""
    for i in range(retries + 1):
        try:
            channel = await bot.client.fetch_public_channel(channel_id)
            await channel.send(f"{text}")
            return
        except Exception as e:
            if i == retries:
                logger.error(f"发送提醒失败 (频道{channel_id[:6]}...): {e}")
            else:
                await asyncio.sleep(1)

# ====================== 获取频道/服务器信息 ======================
channel_cache = {}  # 缓存频道信息

async def get_channel_info(channel_id: str):
    """获取频道信息（包含名称）"""
    if channel_id in channel_cache:
        return channel_cache[channel_id]
    
    try:
        channel = await bot.client.fetch_public_channel(channel_id)
        info = {
            "id": channel_id,
            "name": getattr(channel, 'name', '未知频道'),
            "guild_id": getattr(channel, 'guild_id', None)
        }
        channel_cache[channel_id] = info
        return info
    except Exception as e:
        logger.error(f"获取频道信息失败: {e}")
        return {"id": channel_id, "name": "未知频道", "guild_id": None}

async def get_guild_name(guild_id: str):
    """获取服务器名称"""
    if not guild_id:
        return "未知服务器"
    
    try:
        guild = await bot.client.fetch_guild(guild_id)
        return getattr(guild, 'name', '未知服务器')
    except Exception as e:
        logger.error(f"获取服务器信息失败: {e}")
        return "未知服务器"

# ====================== 任务加载与调度 ======================
def load_task_configs():
    global channel_next_id
    if not os.path.exists(TASK_FILE):
        return

    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    except json.JSONDecodeError:
        logger.error("tasks.json 损坏，尝试备份并重置")
        if os.path.exists(TASK_FILE):
            os.rename(TASK_FILE, f"{TASK_FILE}.bak.{int(os.time())}")
        return

    channel_next_id = config_data.get("channel_next_id", {})
    task_configs = config_data.get("task_configs", [])

    for config in task_configs:
        try:
            inner_id = config["inner_id"]
            show_id = config["show_id"]
            task_type = config["type"]
            params = config["params"]
            content = config["content"]
            channel_id = config["channel_id"]
            rule_desc = config["rule_desc"]

            # 构建触发器
            trigger = None
            if task_type == "interval":
                trigger = IntervalTrigger(**params)
            elif task_type == "cron":
                trigger = CronTrigger(**params, timezone="Asia/Shanghai")

            if not trigger:
                continue

            # 【专家修复】使用 functools.partial 或工厂函数避免闭包陷阱
            # 这里我们定义一个明确的包装器
            def make_job(cid, txt):
                async def job_func():
                    await safe_send_reminder(cid, txt)
                return job_func

            # 如果调度器里已有旧任务（比如热重载），先移除
            if scheduler.get_job(inner_id):
                scheduler.remove_job(inner_id)

            job = scheduler.add_job(
                make_job(channel_id, content),
                trigger,
                id=inner_id,
                misfire_grace_time=300 # 容错：错过5分钟内的任务仍执行
            )

            runtime_tasks[inner_id] = {
                "job": job,
                "show_id": show_id,
                "type": task_type,
                "params": params,
                "content": content,
                "channel_id": channel_id,
                "rule_desc": rule_desc
            }
            logger.info(f"恢复任务: {show_id} @ 频道{channel_id[:6]}...")
        except Exception as e:
            logger.error(f"加载任务失败: {e}", exc_info=True)

# ====================== 指令定义 ======================
@bot.command(name='提醒')
async def add_reminder(msg: Message, *args):
    if len(args) < 3:
        await msg.reply("❌ 格式：\n/提醒 分钟 1 吃饭\n/提醒 每天 12:00 喝水\n/提醒 每周 四 19:00 开会")
        return

    cid = msg.ctx.channel.id
    if cid not in channel_next_id:
        channel_next_id[cid] = 1
    num = channel_next_id[cid]
    show_id = f"r{num}"
    inner_id = f"task_{cid}_{show_id}"

    try:
        trigger = None
        rule = ""
        t_type = ""
        params = {}
        content = ""

        if args[0] == "分钟":
            interval = int(args[1])
            content = " ".join(args[2:])
            trigger = IntervalTrigger(minutes=interval)
            rule = f"每{interval}分钟"
            t_type = "interval"
            params = {"minutes": interval}
        elif args[0] == "小时":
            interval = int(args[1])
            content = " ".join(args[2:])
            trigger = IntervalTrigger(hours=interval)
            rule = f"每{interval}小时"
            t_type = "interval"
            params = {"hours": interval}
        elif args[0] == "每天":
            time_str = args[1]
            content = " ".join(args[2:])
            if not TIME_PATTERN.match(time_str):
                await msg.reply("❌ 时间格式：12:00")
                return
            h, m = map(int, time_str.split(":"))
            trigger = CronTrigger(hour=h, minute=m, timezone="Asia/Shanghai")
            rule = f"每天 {time_str}"
            t_type = "cron"
            params = {"hour": h, "minute": m}
        elif args[0] == "每周":
            weekday = args[1]
            time_str = args[2]
            content = " ".join(args[3:])
            if weekday not in WEEKDAY_MAP:
                await msg.reply("❌ 请用 一~日 或 1~7")
                return
            wd = WEEKDAY_MAP[weekday]
            if not TIME_PATTERN.match(time_str):
                await msg.reply("❌ 时间格式：19:00")
                return
            h, m = map(int, time_str.split(":"))
            trigger = CronTrigger(day_of_week=wd, hour=h, minute=m, timezone="Asia/Shanghai")
            rule = f"每周{CN_WEEKDAY[wd]} {time_str}"
            t_type = "cron"
            params = {"day_of_week": wd, "hour": h, "minute": m}
        else:
            await msg.reply("❌ 类型：分钟/小时/每天/每周")
            return

        # 定义发送函数
        async def job_func():
            await safe_send_reminder(cid, content)

        # 去重：如果ID已存在（比如手动删了json但没重启），先移除
        if scheduler.get_job(inner_id):
            scheduler.remove_job(inner_id)
            if inner_id in runtime_tasks:
                del runtime_tasks[inner_id]

        job = scheduler.add_job(job_func, trigger, id=inner_id, misfire_grace_time=300)

        runtime_tasks[inner_id] = {
            "job": job,
            "show_id": show_id,
            "type": t_type,
            "params": params,
            "content": content,
            "channel_id": cid,
            "rule_desc": rule
        }

        channel_next_id[cid] += 1
        save_task_configs()
        await msg.reply(f"✅ {show_id}｜{rule}：{content}")

    except Exception as e:
        logger.error(f"创建失败: {e}", exc_info=True)
        await msg.reply(f"❌ 失败：{str(e)}")

@bot.command(name='查看提醒')
async def list_reminder(msg: Message):
    cid = msg.ctx.channel.id
    tasks = [t for t in runtime_tasks.values() if t["channel_id"] == cid]
    if not tasks:
        await msg.reply("📭 本频道暂无提醒")
        return
    txt = "📋 本频道提醒：\n"
    for t in tasks:
        txt += f"{t['show_id']}｜{t['rule_desc']}｜{t['content']}\n"
    await msg.reply(txt)

@bot.command(name='删除提醒')
async def del_reminder(msg: Message, show_id: str):
    cid = msg.ctx.channel.id
    target_inner_id = None
    for inner_id, task in runtime_tasks.items():
        if task["channel_id"] == cid and task["show_id"] == show_id:
            target_inner_id = inner_id
            break

    if not target_inner_id:
        await msg.reply(f"❌ 本频道没有 {show_id}")
        return

    try:
        if scheduler.get_job(target_inner_id):
            scheduler.remove_job(target_inner_id)
        del runtime_tasks[target_inner_id]
        save_task_configs()
        await msg.reply(f"✅ 已删除 {show_id}")
    except Exception as e:
        await msg.reply(f"❌ 删除失败：{str(e)}")

@bot.command(name='测试')
async def test_bot(msg: Message):
    await msg.reply("""👋 在线！
📌 指令：
▸ /提醒 分钟 1 吃饭
▸ /提醒 每天 12:00 喝水
▸ /提醒 每周 四 19:00 开会
▸ /查看提醒
▸ /删除提醒 r1""")

@bot.command(name='查看用户')
async def list_users(msg: Message):
    """查看服务器用户列表及状态"""
    try:
        guild_id = msg.ctx.guild.id
        guild = await bot.client.fetch_guild(guild_id)
        
        # 获取服务器用户列表
        users = await guild.fetch_user_list()
        
        if not users:
            await msg.reply("📭 本服务器暂无成员")
            return
        
        txt = f"📋 服务器成员列表（共 {len(users)} 人）：\n"
        for user in users:
            # 获取用户基本信息
            username = getattr(user, 'username', '未知')
            nickname = getattr(user, 'nickname', '')
            user_status = getattr(user, 'status', 0)
            online = getattr(user, 'online', False)
            
            # status 说明：0-离线，1-在线，2-忙碌，3-勿扰，4-隐身
            status_map = {0: "离线", 1: "在线", 2: "忙碌", 3: "勿扰", 4: "隐身"}
            status_text = status_map.get(user_status, f"未知({user_status})")
            
            # 显示在线状态
            online_icon = "🟢" if online else "⚪"
            
            txt += f"{online_icon} {nickname or username} ({username}) - {status_text}\n"
        
        await msg.reply(txt[:2000])  # 限制长度
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}", exc_info=True)
        await msg.reply(f"❌ 获取失败：{str(e)}")

# ====================== 生命周期 ======================
@bot.on_startup
async def start(_):
    load_task_configs()
    scheduler.start()
    
    # 初始化 API 服务器
    api_server.init_bot_manager(
        bot, scheduler, runtime_tasks, 
        channel_next_id, save_task_configs, safe_send_reminder,
        get_channel_info, get_guild_name
    )
    
    # 在 Bot 的事件循环中启动 API 服务器
    import asyncio
    loop = asyncio.get_event_loop()
    
    # 在后台任务中运行 API 服务器
    async def run_api():
        # 导入 uvicorn
        import uvicorn
        config = uvicorn.Config(api_server.app, host="0.0.0.0", port=8000, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    
    # 创建后台任务
    asyncio.create_task(run_api())
    logger.info("✅ 机器人启动成功 (专家优化版 + Web 管理后台)")

if __name__ == "__main__":
    bot.run()