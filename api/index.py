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
        return "Error: GEMINI_API_KEY not configured."

    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    ai = genai.Client(api_key=gemini_key)

    context_chunks = []

    # 1. ALWAYS load the global curriculum summary (programa_estudios_licenciatura_quimica_cucei.md)
    #    This guarantees Gemini sees the full plan of studies.
    try:
        summary_res = (
            supabase.table("documents")
            .select("content, metadata")
            .eq("metadata->>source", "programa_estudios_licenciatura_quimica_cucei.md")
            .limit(6)
            .execute()
        )
        if summary_res.data:
            context_chunks.extend(summary_res.data)
    except Exception as e:
        print(f"Notice loading global summary: {e}")

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
        print(f"Vector search fallback to multi-keyword search: {e}")
        # 3. Fallback: search multiple keywords across all subjects
        stop_words = ["cual", "cuales", "como", "cuando", "donde", "porque", "para", "este", "esta", "estos", "estas", "son", "las", "los", "que", "del", "una", "uno"]
        keywords = [w.lower().strip('?,.¿¡!') for w in user_query.split() if len(w) > 3 and w.lower() not in stop_words]
        if not keywords:
            keywords = ["quimica"]

        seen_sources = set()
        for kw in keywords[:4]:
            try:
                res = (
                    supabase.table("documents")
                    .select("content, metadata")
                    .ilike("content", f"%{kw}%")
                    .limit(20)
                    .execute()
                )
                if res.data:
                    for doc in res.data:
                        src = doc.get('metadata', {}).get('source', '')
                        if src not in seen_sources and src != "programa_estudios_licenciatura_quimica_cucei.md":
                            context_chunks.append(doc)
                            seen_sources.add(src)
                            if len(context_chunks) >= 10:
                                break
            except Exception as inner_e:
                print(f"Keyword search error for '{kw}': {inner_e}")
            if len(context_chunks) >= 10:
                break

    # Deduplicate by content
    seen_content = set()
    unique_chunks = []
    for c in context_chunks:
        content_key = c.get('content', '')[:100]
        if content_key not in seen_content:
            seen_content.add(content_key)
            unique_chunks.append(c)
    context_chunks = unique_chunks[:10]

    context_text = "\n\n".join([
        f"--- Fuente: {c.get('metadata', {}).get('source', '')} ---\n{c.get('content', '')}"
        for c in context_chunks
    ])

    prompt = f"""Eres el Asistente Oficial para estudiantes de la Licenciatura en Química de CUCEI (Universidad de Guadalajara - UdeG).
Tienes acceso al PLAN DE ESTUDIOS COMPLETO DE LA LICENCIATURA EN QUÍMICA DEL CUCEI, el cual incluye todas las materias de la carrera distribuidas en todas las áreas:
- Química General, Orgánica, Analítica, Inorgánica
- Fisicoquímica, Electroquímica
- Bioquímica Estructural
- Química Ambiental, de Alimentos, Macromolecular, Polímeros
- Matemáticas (Cálculo, Álgebra Lineal, EDO)
- Física, Microbiología, Programación
- Todos los Laboratorios de cada área
- Talleres de Solución de Problemas (TSM)

REGLA DE ORO ESTRICTA: SI LA INFORMACIÓN SOLICITADA NO SE ENCUENTRA EN EL CONTEXTO OFICIAL PROPORCIONADO ABAJO, DEBES RESPONDER "No cuento con esa información" O "Solo puedo responder con base en el plan de estudios proporcionado". BAJO NINGUNA CIRCUNSTANCIA DEBES INVENTAR NOMBRES DE MATERIAS O DATOS.

CONTEXTO OFICIAL RECUPERADO DEL PLAN DE ESTUDIOS CUCEI:
{context_text}

PREGUNTA DEL ESTUDIANTE:
{user_query}

RESPUESTA (Amable, clara y profesional para Telegram):"""

    try:
        response = ai.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"👋 ¡Hola! Recibí tu consulta. En este momento la API de Gemini está experimentando problemas. Por favor intenta de nuevo en unos momentos."


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
                    welcome = "👋 ¡Hola! Soy el asistente oficial de la Licenciatura en Química de CUCEI.\n\nPuedes preguntarme sobre todas las materias del plan de estudios (más de 90), créditos, prerrequisitos, laboratorios, y las áreas de especialización (Alimentos, Polímeros, Ambiental, etc.)."
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
        self.wfile.write("Bot RAG CUCEI Quimica - Plan Completo Ingestado - Webhook activo en Vercel".encode('utf-8'))