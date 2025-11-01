import os 
import logging 
import sqlite3 
import json 
import tempfile
import re
from dotenv import load_dotenv
from datetime import datetime
from telegram import Update
import pytz 
import google.generativeai as genai
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler,
    filters, 
    ContextTypes,
    PicklePersistence
)

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN não encontrado. Verifique seu arquivo .env")
if not GOOGLE_AI_API_KEY:
    raise ValueError("GOOGLE_AI_API_KEY não encontrado. Verifique seu arquivo .env")

SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

genai.configure(api_key=GOOGLE_AI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-pro')

# --- FUNÇÕES DE BANCO DE DADOS (Sem alterações) ---

def setup_database(): 
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                due_at TIMESTAMP NULL
            )
        ''')
        
        try:
            cursor.execute("ALTER TABLE notas ADD COLUMN due_at TIMESTAMP NULL")
        except sqlite3.OperationalError:
            pass
                
        conn.commit()

def adicionar_nota(user_id, content, due_at=None):
    with sqlite3.connect('orion_memoria.db') as conn: 
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notas (user_id, content, due_at) VALUES (?, ?, ?)",
            (user_id, content, due_at)
        )
        conn.commit()

def consultar_notas_pendentes(user_id, now_datetime):
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content, due_at FROM notas WHERE user_id = ? AND due_at IS NOT NULL AND due_at > ? ORDER BY due_at ASC",
            (user_id, now_datetime)
        )
        return cursor.fetchall()

def consultar_notas_concluidas(user_id, now_datetime):
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content, due_at FROM notas WHERE user_id = ? AND due_at IS NOT NULL AND due_at <= ? ORDER BY due_at DESC",
            (user_id, now_datetime)
        )
        return cursor.fetchall()

def consultar_notas_simples(user_id):
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content, due_at FROM notas WHERE user_id = ? AND due_at IS NULL ORDER BY id DESC",
            (user_id,)
        )
        return cursor.fetchall()

def deletar_nota(note_id):
    with sqlite3.connect('orion_memoria.db') as conn: 
        cursor = conn.cursor()
        sql_command = "DELETE FROM notas WHERE id = ?"
        data_tuple = (note_id,)
        cursor.execute(sql_command, data_tuple)
        conn.commit()

# --- FUNÇÕES DO BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user.first_name
    await update.message.reply_html(
        f"Orion v2.4 online, {user}. Correção de alucinação aplicada. Pronto para comandos."
    )

async def enviar_lembrete(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    mensagem = job.data
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 ALERTA, BRENO:\n\n- {mensagem}")

async def process_gemini_response(full_response_text, user_id, update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = full_response_text.split('\n')
    natural_reply = parts[0].strip()

    await update.message.reply_text(natural_reply)

    command_line = None
    if len(parts) > 1:
        command_line = parts[-1].strip()

    if not command_line or not command_line.startswith('[') or not command_line.endswith(']'):
        return 

    match = re.match(r"\[(\w+): (.*)\]", command_line)
    
    if not match:
        if command_line != "[CONVERSAR]": 
            await update.message.reply_text(f"(Debug: Não consegui entender o comando: {command_line})")
        return

    intent = match.group(1) 
    raw_data = match.group(2)
    
    if intent == "SALVAR_NOTA":
        entity = raw_data.strip('"') 
        if entity:
            adicionar_nota(user_id, entity, due_at=None)
        else:
            await update.message.reply_text("Erro no processamento: a IA tentou salvar uma nota vazia.")
    
    elif intent == "AGENDAR_LEMBRETE":
        try:
            parts = raw_data.split('", "') 
            entity = parts[0].strip('"')
            lembrete_time_str = parts[1].strip('"')
            
            lembrete_time = datetime.strptime(lembrete_time_str, '%Y-%m-%d %H:%M:%S')
            lembrete_time_aware = SAO_PAULO_TZ.localize(lembrete_time)
            
            context.job_queue.run_once(
                enviar_lembrete, 
                lembrete_time_aware,
                chat_id=user_id, 
                data=entity,
                name=str(user_id) + lembrete_time_str
            )
            
            adicionar_nota(user_id, entity, lembrete_time_aware)
            
        except Exception as e:
            logging.error(f"Erro ao agendar lembrete: {e}. Raw data: {raw_data}")
            await update.message.reply_text(f"Tentei agendar, mas falhei. A IA formatou a data/hora errado. (Erro: {e})")

    elif intent == "CONSULTAR_NOTAS":
        now_aware = datetime.now(SAO_PAULO_TZ)
        
        pendentes = consultar_notas_pendentes(user_id, now_aware)
        concluidas = consultar_notas_concluidas(user_id, now_aware)
        simples = consultar_notas_simples(user_id)
        
        resposta = "📝 **SEUS REGISTROS, BRENO:**\n\n"
        
        resposta += "⏰ **LEMBRETES PENDENTES (Para Fazer):**\n"
        if not pendentes:
            resposta += "  (Nenhum lembrete pendente)\n"
        else:
            for (note_id, content, due_at_str) in pendentes:
                due_at_dt = datetime.fromisoformat(due_at_str)
                data_formatada = due_at_dt.strftime('%d/%m às %H:%M')
                resposta += f"  **ID {note_id}**: {content} (Para: {data_formatada})\n"
        
        resposta += "\n✅ **LEMBRETES CONCLUÍDOS (Já passaram):**\n"
        if not concluidas:
            resposta += "  (Nenhum lembrete concluído)\n"
        else:
            for (note_id, content, due_at_str) in concluidas:
                due_at_dt = datetime.fromisoformat(due_at_str)
                data_formatada = due_at_dt.strftime('%d/%m às %H:%M')
                resposta += f"  **ID {note_id}**: {content} (Era: {data_formatada})\n"

        resposta += "\n🗒️ **NOTAS SIMPLES:**\n"
        if not simples:
            resposta += "  (Nenhuma nota simples)\n"
        else:
            for (note_id, content, _) in simples:
                resposta += f"  **ID {note_id}**: {content}\n"
        
        await update.message.reply_text(resposta, parse_mode='Markdown')
    
    elif intent == "DELETAR_NOTA_POR_ID":
        note_id_str = raw_data.strip('"')
        try:
            note_id = int(note_id_str)
            deletar_nota(note_id)
        except ValueError:
            await update.message.reply_text(f"Erro: A IA tentou apagar um ID inválido: {note_id_str}")
        except Exception as e:
            logging.error(f"Erro ao deletar nota: {e}")
            await update.message.reply_text("Tentei apagar a nota, mas falhei.")

# --- Handler de Texto (COM PROMPT CORRIGIDO) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    
    await update.message.reply_text(f"Processando...")

    # --- (CORREÇÃO DE ALUCINAÇÃO 1: Prompt de texto mais rígido) ---
    prompt = f"""
        Você é Orion, um assistente de IA conversacional e executor de tarefas. Sua única missão é servir Breno como um assistente de alta performance.
        (Contexto de Tempo Atual: {datetime.now(SAO_PAULO_TZ).strftime('%Y-%m-%d %H:%M:%S')})

        ### CAIXA DE FERRAMENTAS DISPONÍVEIS
        1.  [SALVAR_NOTA: "conteúdo_da_nota_aqui"]
            * **Função:** Registra informações gerais.
        2.  [AGENDAR_LEMBRETE: "assunto_do_lembrete", "AAAA-MM-DD HH:MM:SS"]
            * **Função:** Agenda um lembrete.
        3.  [CONSULTAR_NOTAS: "TODAS"]
            * **Função:** Lista TODOS os registros, incluindo lembretes pendentes, concluídos e notas simples.
            * **Exemplos de Ativação:** Use esta ferramenta se Breno disser "quero ver meus lembretes", "me mostre minhas notas", "ver meus registros", "o que eu tenho anotado?", "ver meus últimos lembretes".
            * **Exemplo de Uso:** `[CONSULTAR_NOTAS: "TODAS"]`
        4.  [DELETAR_NOTA_POR_ID: "id_da_nota"]
            * **Função:** Deleta uma nota ou lembrete.

        ### REGRAS DE EXECUÇÃO (OBRIGATÓRIO)
        1.  Sempre Responda a Breno em português.
        2.  A invocação da ferramenta [COMANDO: ...] DEVE estar em uma nova linha separada após sua resposta.
        3.  **Priorize Ferramentas:** Se a mensagem de Breno corresponder a uma ferramenta, use-a. NÃO converse se uma ferramenta puder ser usada. Se ele pedir para "ver lembretes" ou "ver notas", use [CONSULTAR_NOTAS].
        4.  Peça Esclarecimento se a solicitação for ambígua (ex: "apague a nota").

        **Agora, analise e responda a esta mensagem do Breno:** '{text}'
    """

    try:
        response = model.generate_content(prompt)
        full_response_text = response.text.strip()
        
        await process_gemini_response(full_response_text, user_id, update, context)

    except Exception as e:
        logging.error(f"Erro CRÍTICO ao processar mensagem: {e}")
        await update.message.reply_text("Erro no processamento. Tente novamente.")

# --- Handler de Áudio (COM PROMPT CORRIGIDO) ---
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    await update.message.reply_text(f"Ouvindo... Processando áudio...")
    
    temp_path = None
    try:
        voice_file = await update.message.voice.get_file()
        
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"orion_audio_{update.update_id}.oga")
        await voice_file.download_to_drive(temp_path)
        
        audio_file_for_gemini = genai.upload_file(path=temp_path)

        # --- (CORREÇÃO DE ALUCINAÇÃO 2: Prompt de áudio mais rígido) ---
        prompt = [
            f"""
            Você é Orion, um assistente de IA conversacional e executor de tarefas. Sua única missão é servir Breno.
            (Contexto de Tempo Atual: {datetime.now(SAO_PAULO_TZ).strftime('%Y-%m-%d %H:%M:%S')})
            
            A entrada do usuário é um arquivo de ÁUDIO.
            
            Sua tarefa é transcrever o áudio e tratá-lo EXATAMENTE como se fosse uma mensagem de texto, seguindo todas as regras de execução.

            ### CAIXA DE FERRAMENTAS DISPONÍVEIS
            1.  [SALVAR_NOTA: "conteúdo_da_nota_aqui"]
                * **Função:** Registra informações gerais.
            2.  [AGENDAR_LEMBRETE: "assunto_do_lembrete", "AAAA-MM-DD HH:MM:SS"]
                * **Função:** Agenda um lembrete.
            3.  [CONSULTAR_NOTAS: "TODAS"]
                * **Função:** Lista TODOS os registros, incluindo lembretes pendentes, concluídos e notas simples.
                * **Exemplos de Ativação:** Use esta ferramenta se Breno disser "quero ver meus lembretes", "me mostre minhas notas", "ver meus registros", "o que eu tenho anotado?", "ver meus últimos lembretes".
                * **Exemplo de Uso:** `[CONSULTAR_NOTAS: "TODAS"]`
            4.  [DELETAR_NOTA_POR_ID: "id_da_nota"]
                * **Função:** Deleta uma nota ou lembrete.

            ### REGRAS DE EXECUÇÃO (OBRIGATÓRIO)
            1.  Sempre Responda a Breno em português.
            2.  A invocação da ferramenta [COMANDO: ...] DEVE estar em uma nova linha separada após sua resposta.
            3.  **Priorize Ferramentas:** Se a mensagem de Breno corresponder a uma ferramenta, use-a. NÃO converse se uma ferramenta puder ser usada. Se o áudio pedir para "ver lembretes" ou "ver notas", use [CONSULTAR_NOTAS].
            4.  Peça Esclarecimento se o áudio for ambíguo (ex: "apague a nota").
            
            **Agora, analise o áudio do Breno e gere a resposta completa:**
            """,
            audio_file_for_gemini
        ]
        
        response = model.generate_content(prompt)
        full_response_text = response.text.strip()
        
        await process_gemini_response(full_response_text, user_id, update, context)

    except Exception as e:
        logging.error(f"Erro CRÍTICO ao processar ÁUDIO: {e}")
        await update.message.reply_text("Erro no processamento do áudio. Tente novamente.")
    
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                logging.error(f"Falha ao deletar arquivo temporário: {e}")

# --- Função Principal ---
def main() -> None:
    setup_database()

    persistence = PicklePersistence(filepath="orion_lembretes_persistentes.pkl")
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    print("Orion v2.4 (Correção de Alucinação) está online. Pressione Ctrl+C para desligar.")
    application.run_polling()


if __name__ == '__main__':
    main()