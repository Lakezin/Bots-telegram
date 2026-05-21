
import os
import sqlite3
from datetime import datetime, timedelta
from threading import Thread

import mercadopago
import requests
from dotenv import load_dotenv
from flask import Flask, request
from io import BytesIO
import base64

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("VIP_TELEGRAM_TOKEN")
MP_TOKEN = os.getenv("MP_TOKEN")
ID_GRUPO_VIP = int(os.getenv("ID_GRUPO_VIP", "0"))

sdk = mercadopago.SDK(MP_TOKEN)


def init_db():
    conn = sqlite3.connect("bot.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pagamentos (
            payment_id INTEGER PRIMARY KEY,
            chat_id    INTEGER NOT NULL,
            status     TEXT DEFAULT 'pending',
            criado_em  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def salvar_pagamento(payment_id: int, chat_id: int):
    conn = sqlite3.connect("bot.db")
    conn.execute(
        "INSERT OR REPLACE INTO pagamentos (payment_id, chat_id) VALUES (?, ?)",
        (payment_id, chat_id),
    )
    conn.commit()
    conn.close()


def buscar_chat_id(payment_id: int):
    conn = sqlite3.connect("bot.db")
    row = conn.execute(
        "SELECT chat_id FROM pagamentos WHERE payment_id = ?", (payment_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def marcar_aprovado(payment_id: int):
    conn = sqlite3.connect("bot.db")
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
        InlineKeyboardButton("❌ NÃO, Recusar oferta",  callback_data="nao"),
    ]]
    reply_markup = InlineKeyboardMarkup(teclado)

    try:
        await update.message.reply_video(
            video=open("video.mp4", "rb"),
            caption=(
                "🔥 *ACESSO VIP LIBERADO*\n"
                "✅ Vitalício\n"
                "✅ +6 grupos exclusivos\n"
                "✅ Conteúdo atualizado diariamente\n"
                "✅ Acesso imediato\n\n"
                "💰 Apenas R$9,90"
            ),
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "🔥 *ACESSO VIP LIBERADO* — R$9,90",
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

    await query.message.reply_text("⏳ Gerando pagamento...")

    valor = 9.90
    expiracao = (datetime.utcnow() + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.000-03:00"
    )

    payment_data = {
        "transaction_amount": valor,
        "description":        "VIP Grupos",
        "payment_method_id":  "pix",
        "date_of_expiration": expiracao,
        "payer": {
            "email":      "cliente@email.com",
            "first_name": "Cliente",
            "last_name":  "VIP",
        },
    }

    try:
        response = sdk.payment().create(payment_data)
        payment = response["response"]

        if "id" not in payment:
            raise ValueError(f"Resposta inesperada do MP: {payment}")

        payment_id = payment["id"]
        poi = payment.get("point_of_interaction", {})
        tx_data = poi.get("transaction_data", {})
        qr_code = tx_data.get("qr_code")
        qr_base64 = tx_data.get("qr_code_base64")

        if not qr_code or not qr_base64:
            await query.message.reply_text("❌ Erro ao gerar QR Code. Tente novamente.")
            return

        salvar_pagamento(payment_id, query.message.chat.id)

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
        print(f"Erro ao gerar pagamento: {e}")
        await query.message.reply_text("❌ Ocorreu um erro. Tente novamente mais tarde.")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text

    if texto == "💎 Comprar VIP":
        await update.message.reply_text("💎 VIP — R$9,90\n\nUse /start para ver a oferta.")
    elif texto == "📞 Suporte":
        await update.message.reply_text("Suporte: @mthlake")


app_flask = Flask(__name__)


@app_flask.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    print("Webhook recebido:", data)

    if data.get("type") != "payment":
        return "ok", 200

    try:
        payment_id = int(data["data"]["id"])
        payment_info = sdk.payment().get(payment_id)
        payment = payment_info["response"]
        status = payment.get("status")

        print(f"Pagamento {payment_id} — status: {status}")

        if status != "approved":
            return "ok", 200

        chat_id = buscar_chat_id(payment_id)
        if not chat_id:
            print("chat_id não encontrado para", payment_id)
            return "ok", 200

        marcar_aprovado(payment_id)

        mensagem = "✅ *Pagamento aprovado!*\n\n🔗 Seu link exclusivo (uso único):\nhttps://t.me/Retafinal_bot?start=go_zhveburd77dgj9gj"

        if ID_GRUPO_VIP:
            invite_resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
                json={
                    "chat_id":      ID_GRUPO_VIP,
                    "member_limit": 1,
                },
            ).json()

            link = invite_resp.get("result", {}).get("invite_link")
            if link:
                mensagem += f"🔗 Seu link exclusivo (uso único):\n{link}"
            else:
                mensagem += "🔗Seu link exclusivo (uso único):\nhttps://t.me/Retafinal_bot?start=vip"
       
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       mensagem,
                "parse_mode": "Markdown",
            },
        )

    except Exception as e:
        print(f"Erro no webhook: {e}")

    return "ok", 200


def run_flask():
    app_flask.run(port=5000)


Thread(target=run_flask, daemon=True).start()

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, menu))
app.add_handler(CallbackQueryHandler(botoes))

print("✅ Bot rodando...")
app.run_polling()
