import os
import json
from http.server import BaseHTTPRequestHandler
import requests

from supabase import create_client, Client
from google import genai

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://jxgozvamlggrochmxmro.supabase.co").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_TOAxsHC8PytOuT2uPjsvww_xzJt2-xm").strip()

gemini_key = os.environ.get('GEMINI_API_KEY', '').strip()
telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()

def send_telegram_message(chat_id, text):
    if not telegram_token:
        return "Error: TELEGRAM_BOT_TOKEN missing"
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return f"Status: {r.status_code}, Body: {r.text}"
    except Exception as e:
        return f"Exception: {e}"

def get_rag_response(user_query: str) -> str:
    if not gemini_key:
        return "Error: GEMINI_API_KEY non-configured."
        
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    ai = genai.Client(api_key=gemini_key)
    
    context_chunks = []
    
    # 1. Always fetch global curriculum summary for broad major-wide context
    try:
        main_summary_res = supabase.table("documents").select("content, metadata").ilike("content", "%programa_estudios_licenciatura_quimica_cucei%").limit(3).execute()
        if main_summary_res.data:
            context_chunks.extend(main_summary_res.data)
    except Exception as e:
        print("Notice loading main summary:", e)

    # 2. Try vector embedding search for specific question details
    try:
        embed_res = ai.models.embed_content(
            model="models/gemini-embedding-001",
            contents=user_query
        )
        query_vector = embed_res.embeddings[0].values
        
        rpc_res = supabase.rpc("match_documents", {
            "query_embedding": query_vector,
            "match_count": 5,
            "filter": {}
        }).execute()
        
        if rpc_res.data:
            context_chunks.extend(rpc_res.data)
    except Exception as e:
        print(f"Vector search fallback to keyword search: {e}")
        # 3. Fallback to keyword search across all areas
        keywords = [w for w in user_query.split() if len(w) > 3]
        q_term = keywords[0] if keywords else "quimica"
        res = supabase.table("documents").select("content, metadata").ilike("content", f"%{q_term}%").limit(4).execute()
        if res.data:
            context_chunks.extend(res.data)

    context_text = "\n\n".join([
        f"--- Fuente: {c.get('metadata', {}).get('source', '')} ---\n{c.get('content', '')}"
        for c in context_chunks
    ])

    prompt = f"""Eres el Asistente Oficial para estudiantes de la Licenciatura en Química de CUCEI (Universidad de Guadalajara - UdeG).
Responde amablemente a la pregunta del estudiante sobre cualquier área o aspecto del plan de estudios de la carrera de Química en general.

CONTEXTO OFICIAL COMPLETO DEL PLAN DE ESTUDIOS DE QUÍMICA CUCEI:
{context_text}

PREGUNTA DEL ESTUDIANTE:
{user_query}

RESPUESTA (Amable, clara y abarcando toda la carrera de Química):"""

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"👋 ¡Hola! Recibí tu consulta. En este momento la API de Gemini está reiniciando cuotas. Por favor intenta de nuevo en unos momentos."

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        tg_status = "No message processed"
        try:
            update = json.loads(post_data.decode('utf-8'))
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = message.get("chat_id") or message.get("chat", {}).get("id")
            
            if text and chat_id:
                if text.startswith("/start"):
                    welcome = "👋 ¡Hola! Soy el asistente oficial de la Licenciatura en Química de CUCEI.\n\nPuedes preguntarme sobre cualquiera de las 9 áreas de la carrera, materias, créditos, prerrequisitos y laboratorios."
                    tg_status = send_telegram_message(chat_id, welcome)
                else:
                    bot_reply = get_rag_response(text)
                    tg_status = send_telegram_message(chat_id, bot_reply)
        except Exception as e:
            tg_status = f"Error handling webhook: {e}"
            
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "telegram_result": tg_status}).encode('utf-8'))

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot RAG CUCEI Telegram Webhook 24/7 activo en Vercel".encode('utf-8'))
