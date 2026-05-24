import os
from datetime import datetime, timezone, date

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="AMERICO AI Email Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
FROM_NAME = os.getenv("FROM_NAME", "AMERICO AI")


class SendPendingRequest(BaseModel):
    limit: int = 20


class RenewalReminderRequest(BaseModel):
    limit: int = 100


def supabase_headers():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def check_env():
    if not SUPABASE_URL:
        raise HTTPException(status_code=500, detail="Falta SUPABASE_URL")
    if not SUPABASE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Falta SUPABASE_SECRET_KEY")
    if not BREVO_API_KEY:
        raise HTTPException(status_code=500, detail="Falta BREVO_API_KEY")
    if not FROM_EMAIL:
        raise HTTPException(status_code=500, detail="Falta FROM_EMAIL")


def parse_supabase_date(value):
    if not value:
        return None

    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).date()
    except Exception:
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None


def send_email_brevo(to_email: str, subject: str, message: str):
    url = "https://api.brevo.com/v3/smtp/email"

    html = f"""
    <div style="font-family: Arial, sans-serif; background:#070b14; padding:24px;">
      <div style="max-width:640px; margin:auto; background:#101827; border-radius:18px; padding:28px; color:#ffffff;">
        <h1 style="color:#10b981; margin:0 0 12px 0;">AMERICO AI</h1>
        <h2 style="color:#ffffff; margin:0 0 18px 0;">{subject}</h2>
        <p style="color:#d1d5db; font-size:16px; line-height:1.7; white-space:pre-line;">{message}</p>
        <hr style="border:none; border-top:1px solid #243044; margin:26px 0;" />
        <p style="font-size:12px; color:#9ca3af;">
          Mensaje oficial de AMERICO AI.
        </p>
      </div>
    </div>
    """

    payload = {
        "sender": {
            "name": FROM_NAME,
            "email": FROM_EMAIL,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }

    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    if response.status_code >= 400:
        raise Exception(response.text)

    return response.json()


def get_pending_emails(limit: int):
    url = f"{SUPABASE_URL}/rest/v1/email_queue"
    params = {
        "estado": "eq.pendiente",
        "select": "*",
        "order": "created_at.asc",
        "limit": str(limit),
    }

    response = requests.get(url, headers=supabase_headers(), params=params, timeout=30)

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Error leyendo email_queue: {response.text}",
        )

    return response.json()


def update_email_status(row_id: str, estado: str, error: str | None = None):
    url = f"{SUPABASE_URL}/rest/v1/email_queue"

    payload = {
        "estado": estado,
        "error": error,
    }

    if estado == "enviado":
        payload["enviado_at"] = datetime.now(timezone.utc).isoformat()

    response = requests.patch(
        url,
        headers=supabase_headers(),
        params={"id": f"eq.{row_id}"},
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Error actualizando email_queue: {response.text}",
        )


def insert_email_queue(email: str, asunto: str, mensaje: str):
    url = f"{SUPABASE_URL}/rest/v1/email_queue"

    payload = {
        "email": email,
        "asunto": asunto,
        "mensaje": mensaje,
        "estado": "pendiente",
    }

    response = requests.post(
        url,
        headers=supabase_headers(),
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Error insertando email_queue: {response.text}",
        )

    return response.json()


def already_queued_today(email: str, asunto: str):
    today = datetime.now(timezone.utc).date().isoformat()
    url = f"{SUPABASE_URL}/rest/v1/email_queue"

    params = {
        "email": f"eq.{email}",
        "asunto": f"eq.{asunto}",
        "created_at": f"gte.{today}T00:00:00+00:00",
        "select": "id",
        "limit": "1",
    }

    response = requests.get(url, headers=supabase_headers(), params=params, timeout=30)

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Error verificando duplicados: {response.text}",
        )

    return len(response.json()) > 0


def process_pending_emails(limit: int):
    rows = get_pending_emails(limit)

    result = {
        "total_found": len(rows),
        "sent": 0,
        "errors": 0,
        "items": [],
    }

    for row in rows:
        row_id = row.get("id")
        email = row.get("email")
        asunto = row.get("asunto")
        mensaje = row.get("mensaje")

        try:
            if not row_id or not email or not asunto or not mensaje:
                raise Exception("Registro incompleto en email_queue")

            send_email_brevo(email, asunto, mensaje)
            update_email_status(row_id, "enviado", None)

            result["sent"] += 1
            result["items"].append({
                "email": email,
                "status": "enviado",
            })

        except Exception as e:
            if row_id:
                update_email_status(row_id, "error", str(e))

            result["errors"] += 1
            result["items"].append({
                "email": email,
                "status": "error",
                "error": str(e),
            })

    return result


def get_users_for_reminders(limit: int):
    url = f"{SUPABASE_URL}/rest/v1/usuarios"

    params = {
        "select": "email,nombre,plan,estado,rol,fecha_plan_fin",
        "limit": str(limit),
    }

    response = requests.get(url, headers=supabase_headers(), params=params, timeout=30)

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail=f"Error leyendo usuarios: {response.text}",
        )

    return response.json()


@app.get("/")
def home():
    return {
        "app": "AMERICO AI Email Backend",
        "status": "online",
    }


@app.post("/send-pending-emails")
def send_pending_emails(body: SendPendingRequest):
    check_env()

    if body.limit < 1 or body.limit > 100:
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 100")

    return process_pending_emails(body.limit)


@app.post("/send-renewal-reminders")
def send_renewal_reminders(body: RenewalReminderRequest):
    check_env()

    if body.limit < 1 or body.limit > 500:
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 500")

    users = get_users_for_reminders(body.limit)
    today = datetime.now(timezone.utc).date()

    created = 0
    skipped = 0
    expired_created = 0
    errors = []
    items = []

    for user in users:
        email = user.get("email")
        plan = str(user.get("plan") or "").lower().strip()
        estado = str(user.get("estado") or "").lower().strip()
        rol = str(user.get("rol") or "").lower().strip()
        fecha_plan_fin = parse_supabase_date(user.get("fecha_plan_fin"))

        if not email:
            skipped += 1
            continue

        if rol == "admin":
            skipped += 1
            continue

        if plan in ["gratis", "free", "ilimitado"]:
            skipped += 1
            continue

        if estado != "activo":
            skipped += 1
            continue

        if not fecha_plan_fin:
            skipped += 1
            continue

        dias = (fecha_plan_fin - today).days

        try:
            if dias == 2:
                asunto = "Tu plan AMERICO AI vence en 2 días"
                mensaje = (
                    f"Hola,\n\n"
                    f"Tu plan {user.get('plan')} de AMERICO AI vence el {fecha_plan_fin}.\n\n"
                    f"Renueva tu suscripción para seguir usando tus beneficios sin interrupciones.\n\n"
                    f"Gracias por usar AMERICO AI."
                )

                if already_queued_today(email, asunto):
                    skipped += 1
                    continue

                insert_email_queue(email, asunto, mensaje)
                created += 1

                items.append({
                    "email": email,
                    "type": "vence_en_2_dias",
                    "fecha_plan_fin": str(fecha_plan_fin),
                })

            elif dias < 0:
                asunto = "Tu plan AMERICO AI ha expirado"
                mensaje = (
                    f"Hola,\n\n"
                    f"Tu plan {user.get('plan')} de AMERICO AI expiró el {fecha_plan_fin}.\n\n"
                    f"Renueva tu suscripción para recuperar tus beneficios y seguir usando la plataforma.\n\n"
                    f"Gracias por usar AMERICO AI."
                )

                if already_queued_today(email, asunto):
                    skipped += 1
                    continue

                insert_email_queue(email, asunto, mensaje)
                expired_created += 1

                items.append({
                    "email": email,
                    "type": "plan_expirado",
                    "fecha_plan_fin": str(fecha_plan_fin),
                })

            else:
                skipped += 1

        except Exception as e:
            errors.append({
                "email": email,
                "error": str(e),
            })

    send_result = process_pending_emails(100)

    return {
        "checked_users": len(users),
        "created_renewal_reminders": created,
        "created_expired_reminders": expired_created,
        "skipped": skipped,
        "errors": errors,
        "queued_items": items,
        "send_result": send_result,
    }
