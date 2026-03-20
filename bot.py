import os
import re
import asyncio
import logging
import aiosqlite
from dotenv import load_dotenv
from telethon import TelegramClient, events
import ccxt.async_support as ccxt

# =====================
# LOAD ENV
# =====================
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE_NUMBER")
CHANNEL = os.getenv("CHANNEL_NAME")

BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

# =====================
# LOGGING
# =====================
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

# =====================
# DATABASE
# =====================
async def init_db():
    async with aiosqlite.connect("trades.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            margin REAL,
            result TEXT
        )
        """)
        await db.commit()

# =====================
# BINANCE
# =====================
exchange = ccxt.binance({
    "apiKey": BINANCE_KEY,
    "secret": BINANCE_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "future"},
})

# =====================
# GLOBAL STATE
# =====================
running_trades = 0
MAX_TRADES = 2
base_margin = 100
sl_streak = 0

# =====================
# PARSER
# =====================
def parse_signal(text):

    symbol = re.search(r"#([A-Z]+/USDT)", text).group(1).replace("/", "")
    side = "LONG" if "Long" in text else "SHORT"

    entry = float(re.findall(r"([\d.]+)", text.split("Buy")[1])[0])
    tp = re.findall(r"([\d.]+)", text.split("Target")[1])
    tp1, tp2 = float(tp[0]), float(tp[1])
    sl = float(re.search(r"StopLoss:\s*([\d.]+)", text).group(1))

    return symbol, side, entry, tp1, tp2, sl

# =====================
# BALANCE CHECK
# =====================
async def balance_ok():
    bal = await exchange.fetch_balance()
    usdt = bal['total']['USDT']
    return usdt >= 100

# =====================
# TP CHECK
# =====================
async def tp_hit(symbol, side, tp1, tp2):

    price = (await exchange.fetch_ticker(symbol))['last']

    if side == "LONG":
        return price >= tp1 or price >= tp2
    else:
        return price <= tp1 or price <= tp2

# =====================
# EXECUTE TRADE
# =====================
async def trade(signal):

    global running_trades, base_margin, sl_streak

    if running_trades >= MAX_TRADES:
        log("Max trades running")
        return

    if not await balance_ok():
        log("Balance < 100")
        return

    symbol, side, entry, tp1, tp2, sl = parse_signal(signal)

    if await tp_hit(symbol, side, tp1, tp2):
        log("TP already hit → skip")
        return

    ticker = await exchange.fetch_ticker(symbol)
    qty = round(base_margin / ticker['last'], 3)

    order_side = "buy" if side == "LONG" else "sell"

    order = await exchange.create_limit_order(
        symbol, order_side, qty, entry
    )

    running_trades += 1
    oid = order['id']
    log(f"Order placed {symbol}")

    # WAIT FILL
    while True:

        o = await exchange.fetch_order(oid, symbol)

        if o['status'] == "closed":
            break

        if await tp_hit(symbol, side, tp1, tp2):
            await exchange.cancel_order(oid, symbol)
            running_trades -= 1
            log("Cancelled before fill")
            return

        await asyncio.sleep(5)

    # TP/SL
    close_side = "sell" if side == "LONG" else "buy"

    await exchange.create_limit_order(symbol, close_side, qty/2, tp1)
    await exchange.create_limit_order(symbol, close_side, qty/2, tp2)

    await exchange.create_order(
        symbol,
        "STOP_MARKET",
        close_side,
        qty,
        None,
        {"stopPrice": sl}
    )

    log("TP/SL placed")

# =====================
# TELEGRAM LISTENER
# =====================
client = TelegramClient("session", API_ID, API_HASH)

@client.on(events.NewMessage(chats=CHANNEL))
async def handler(event):

    text = event.raw_text

    if "StopLoss" in text and "Target" in text:
        log("Signal detected")
        asyncio.create_task(trade(text))

# =====================
# MAIN
# =====================
async def main():
    await init_db()
    await client.start(PHONE)
    log("PRO v4 ELITE STARTED")
    await client.run_until_disconnected()

asyncio.run(main())