import os
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="AMERICO AI Email Backend")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
FROM_NAME = os.getenv("FROM_NAME", "AMERICO AI")


class SendPendingRequest(BaseModel):
    limit: int = 20


def supabase_headers():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


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


@app.get("/")
def home():
    return {
        "app": "AMERICO AI Email Backend",
        "status": "online",
    }


@app.post("/send-pending-emails")
def send_pending_emails(body: SendPendingRequest):
    if not SUPABASE_URL:
        raise HTTPException(status_code=500, detail="Falta SUPABASE_URL")

    if not SUPABASE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Falta SUPABASE_SECRET_KEY")

    if not BREVO_API_KEY:
        raise HTTPException(status_code=500, detail="Falta BREVO_API_KEY")

    if not FROM_EMAIL:
        raise HTTPException(status_code=500, detail="Falta FROM_EMAIL")

    if body.limit < 1 or body.limit > 100:
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 100")

    rows = get_pending_emails(body.limit)

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
