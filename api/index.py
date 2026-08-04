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
        print("Error: TELEGRAM_BOT_TOKEN environment variable is not set.")
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
        return "Error: GEMINI_API_KEY environment variable is not set."
        
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    ai = genai.Client(api_key=gemini_key)
    
    context_chunks = []
    
    # 1. Try vector embedding search
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
        
        context_chunks = rpc_res.data or []
    except Exception as e:
        print(f"Vector search notice (using fallback text search): {e}")
        # 2. Fallback to Supabase ILIKE keyword search
        keywords = [w for w in user_query.split() if len(w) > 3]
        query_word = keywords[0] if keywords else "quimica"
        res = supabase.table("documents").select("content, metadata").ilike("content", f"%{query_word}%").limit(5).execute()
        context_chunks = res.data or []

    context_text = "\n\n".join([
        f"--- Fuente: {c.get('metadata', {}).get('source', '')} ---\n{c.get('content', '')}"
        for c in context_chunks
    ])

    prompt = f"""Eres el Asistente Oficial para estudiantes de la Licenciatura en Química de CUCEI (Universidad de Guadalajara - UdeG).
Responde amablemente a la pregunta del estudiante basándote en la información oficial recuperada del plan de estudios.

CONTEXTO OFICIAL CUCEI:
{context_text}

PREGUNTA DEL ESTUDIANTE:
{user_query}

RESPUESTA (Formato amable y bien estructurado para Telegram):"""

    try:
        response = ai.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"👋 ¡Hola! Recibí tu consulta sobre '{user_query}'. En este momento la API de Gemini está reiniciando cuotas. Por favor intenta de nuevo en unos momentos."

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
                    welcome = "👋 ¡Hola! Soy el asistente oficial de la Licenciatura en Química de CUCEI.\n\nPuedes preguntarme sobre materias, créditos, prerrequisitos y programas analíticos."
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
