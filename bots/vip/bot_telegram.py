import os
import sqlite3
import base64
from io import BytesIO
from datetime import datetime, timedelta
from threading import Thread
from pathlib import Path

import mercadopago
import requests
from dotenv import load_dotenv
from flask import Flask, request
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent.parent / ".env"

load_dotenv(ENV_PATH)

TELEGRAM_TOKEN = os.getenv("VIP_TELEGRAM_TOKEN")
MP_TOKEN = os.getenv("MP_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("VIP_TELEGRAM_TOKEN não encontrado no .env")

if not MP_TOKEN:
    raise ValueError("MP_TOKEN não encontrado no .env")

sdk = mercadopago.SDK(MP_TOKEN)

DB_PATH = BASE_DIR / "bot.db"
VIDEO_PATH = BASE_DIR / "video.mp4"

app_flask = Flask(__name__)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pagamentos (
            payment_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            criado_em TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def salvar_pagamento(payment_id: int, chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO pagamentos (payment_id, chat_id, status) VALUES (?, ?, 'pending')",
        (payment_id, chat_id),
    )
    conn.commit()
    conn.close()
    print(f"✅ Pagamento salvo: {payment_id} | chat_id={chat_id}")


def buscar_chat_id(payment_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT chat_id FROM pagamentos WHERE payment_id = ?",
        (payment_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def marcar_aprovado(payment_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE pagamentos SET status = 'approved' WHERE payment_id = ?",
        (payment_id,),
    )
    conn.commit()
    conn.close()


init_db()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [[
        InlineKeyboardButton("✅ SIM, QUERO +6 GRUPOS", callback_data="vip"),
        InlineKeyboardButton("❌ NÃO, Recusar oferta", callback_data="nao"),
    ]]

    reply_markup = InlineKeyboardMarkup(teclado)

    texto = (
        "🔥 *ACESSO VIP LIBERADO*\n"
        "✅ Vitalício\n"
        "✅ +6 grupos exclusivos\n"
        "✅ Conteúdo atualizado diariamente\n"
        "✅ Acesso imediato\n\n"
        "💰 Apenas R$9,90"
    )

    if VIDEO_PATH.exists():
        with open(VIDEO_PATH, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=texto,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            texto,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "nao":
        await query.message.reply_text("Tudo bem! Se mudar de ideia, é só chamar. 😊")
        return

    if query.data != "vip":
        await query.message.reply_text("Opção inválida.")
        return

    await query.message.reply_text("⏳ Gerando pagamento...")

    valor = 9.90

    expiracao = (datetime.utcnow() + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.000-03:00"
    )

    payment_data = {
        "transaction_amount": valor,
        "description": "VIP Grupos",
        "payment_method_id": "pix",
        "date_of_expiration": expiracao,
        "payer": {
            "email": "cliente@email.com",
            "first_name": "Cliente",
            "last_name": "VIP",
        },
    }

    try:
        response = sdk.payment().create(payment_data)
        payment = response.get("response", {})

        payment_id = payment.get("id")

        if not payment_id:
            print("Resposta inesperada Mercado Pago:", response)
            await query.message.reply_text("❌ Erro ao criar pagamento.")
            return

        tx_data = payment.get("point_of_interaction", {}
                              ).get("transaction_data", {})
        qr_code = tx_data.get("qr_code")
        qr_base64 = tx_data.get("qr_code_base64")

        if not qr_code or not qr_base64:
            print("QR Code não encontrado:", payment)
            await query.message.reply_text("❌ Erro ao gerar QR Code Pix.")
            return

        salvar_pagamento(int(payment_id), query.message.chat.id)

        image_data = base64.b64decode(qr_base64)
        bio = BytesIO(image_data)
        bio.name = "pix.png"

        await query.message.reply_photo(
            photo=bio,
            caption=(
                f"✅ *Pix gerado!*\n\n"
                f"💰 Valor: R$ {valor:.2f}\n"
                f"⏳ Expira em 30 minutos\n"
                f"🆔 ID: `{payment_id}`\n\n"
                f"📋 *Copia e cola:*\n`{qr_code}`"
            ),
            parse_mode="Markdown",
        )

    except Exception as e:
        print("Erro ao gerar pagamento:", e)
        await query.message.reply_text("❌ Ocorreu um erro. Tente novamente mais tarde.")


@app_flask.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    print("Webhook recebido:", data)

    if data.get("type") != "payment":
        return "ok", 200

    try:
        payment_id = int(data["data"]["id"])

        payment_info = sdk.payment().get(payment_id)
        payment = payment_info.get("response", {})

        status = payment.get("status")

        print(f"Pagamento {payment_id} | status: {status}")

        if status != "approved":
            return "ok", 200

        chat_id = buscar_chat_id(payment_id)

        if not chat_id:
            print(f"chat_id não encontrado para payment_id={payment_id}")
            return "ok", 200

        marcar_aprovado(payment_id)

        mensagem = (
            "✅ Pagamento aprovado!\n\n"
            "🔗 Seu acesso foi liberado:\n"
            "https://t.me/Retafinal_bot?start=vip"
        )

        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": mensagem,
            },
            timeout=10,
        )

        print("Resposta Telegram:", resp.json())

    except Exception as e:
        print("Erro no webhook:", e)

    return "ok", 200


def run_flask():
    port = int(os.getenv("PORT", 5000))
    app_flask.run(host="0.0.0.0", port=port)


Thread(target=run_flask, daemon=True).start()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(botoes))

print("✅ Bot rodando...")
app.run_polling()
