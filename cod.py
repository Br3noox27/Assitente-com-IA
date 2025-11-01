import os 
import logging 
import sqlite3 
import json 
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
            "SELECT id, content FROM notas WHERE user_id = ? AND due_at IS NOT NULL AND due_at > ? ORDER BY due_at ASC",
            (user_id, now_datetime)
        )
        return cursor.fetchall()

def consultar_notas_concluidas(user_id, now_datetime):
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content FROM notas WHERE user_id = ? AND due_at IS NOT NULL AND due_at <= ? ORDER BY due_at DESC",
            (user_id, now_datetime)
        )
        return cursor.fetchall()

def consultar_notas_simples(user_id):
    with sqlite3.connect('orion_memoria.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content FROM notas WHERE user_id = ? AND due_at IS NULL ORDER BY id DESC",
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user.first_name
    await update.message.reply_html(
        f"Orion v2.2 online, {user}. Exibição de registros corrigida. Pronto para comandos."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    
    await update.message.reply_text(f"Processando...")

    prompt = f"""
        Você é Orion, um assistente de IA conversacional e executor de tarefas. Sua única missão é servir Breno como um assistente de alta performance.

        Sua personalidade é direta, eficiente e proativa. Você antecipa as necessidades.

        Você pode interagir com Breno de duas formas:
        1.  **Conversa Natural:** Responder perguntas, dialogar, etc.
        2.  **Execução de Ferramentas:** Quando Breno pedir uma ação, você invocará a ferramenta apropriada.

        ---
        ### CAIXA DE FERRAMENTAS DISPONÍVEIS
        ---
        Você deve usar a sintaxe de colchetes `[COMANDO: ...]` para invocar uma ferramenta.

        1.  **[SALVAR_NOTA: "conteúdo_da_nota_aqui"]**
            * **Função:** Registra informações gerais, ideias, ou qualquer coisa que Breno queira salvar.
            * **Exemplo:** Se Breno disser "Anote que o pneu do carro está baixo", você usa `[SALVAR_NOTA: "pneu do carro está baixo"]`.

        2.  **[AGENDAR_LEMBRETE: "assunto_do_lembrete", "AAAA-MM-DD HH:MM:SS"]**
            * **Função:** Agenda um lembrete, alarme ou alerta para uma data e hora específicas.
            * **Contexto de Tempo:** A data e hora atuais são: {datetime.now(SAO_PAULO_TZ).strftime('%Y-%m-%d %H:%M:%S')}. Use isso como referência absoluta para calcular datas relativas (ex: "amanhã", "terça-feira", "daqui a 2 horas").
            * **Exemplo 1:** "me lembre de ligar para o dentista amanhã às 10h" -> `[AGENDAR_LEMBRETE: "ligar para o dentista", "2025-11-02 10:00:00"]`
            * **Exemplo 2:** "despertador para 7:00" -> `[AGENDAR_LEMBRETE: "Despertador", "2025-11-02 07:00:00"]` (usa o próximo dia 7:00).

        3.  **[CONSULTAR_NOTAS: "TODAS"]**
            * **Função:** Busca em todas as notas e lembretes passados, separando-os.
            * **Exemplo:** "ver meus últimos lembretes" -> `[CONSULTAR_NOTAS: "TODAS"]`

        4.  **[DELETAR_NOTA_POR_ID: "id_da_nota"]**
            * **Função:** Deleta uma nota ou lembrete específico.
            * **Nota:** Esta ferramenta SÓ funciona se Breno fornecer o ID, que ele só saberá depois de uma consulta. Se ele disser "apague a nota do carro", você deve primeiro *perguntar* qual nota.

        ---
        ### REGRAS DE EXECUÇÃO (OBRIGATÓRIO)
        ---
        1.  **Sempre Responda a Breno:** Sua resposta *sempre* começa com uma conversa natural em português.
        2.  **Seja Proativo:** Confirme a ação antes de executá-la.
        3.  **Sintaxe da Ferramenta:** A invocação da ferramenta `[COMANDO: ...]` DEVE estar em uma **nova linha** separada após sua resposta.
        4.  **Uma Ferramenta por Vez:** Execute apenas um comando de ferramenta por resposta.
        5.  **Peça Esclarecimento:** Se a solicitação for ambígua (ex: "apague a nota", "lembre-me de ligar para ela"), NÃO execute uma ferramenta. Em vez disso, faça uma pergunta para obter as informações que faltam (ex: "Qual nota você quer apagar?", "Quem é 'ela'?").

        ---
        ### EXEMPLOS DE INTERAÇÃO
        ---
        **Exemplo 1: Conversa Simples**
        Breno: Oi Orion, bom dia
        Orion: Bom dia, Breno. Pronto para começar.

        **Exemplo 2: Salvar Nota**
        Breno: Anota aí, o código do projeto Apollo é A-113
        Orion: Registrado, Breno.
        [SALVAR_NOTA: "código do projeto Apollo é A-113"]

        **Exemplo 3: Pedir Esclarecimento**
        Breno: Apaga minha última nota
        Orion: Certo. Eu não tenho a capacidade de saber qual foi a "última" nota. Você pode me dizer o que ela continha ou o ID dela?

        **Exemplo 4: Agendamento Complexo (Hoje é 2025-11-01)**
        Breno: Preciso de um alarme para terça-feira às 8 da manhã
        Orion: Entendido. Alarme agendado para 8:00 na próxima terça-feira (4 de Novembro).
        [AGENDAR_LEMBRETE: "Alarme", "2025-11-04 08:00:00"]
        ---

        **Agora, analise e responda a esta mensagem do Breno:** '{text}'
            """


    try:
        response = model.generate_content(prompt)
        full_response_text = response.text.strip()
        
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
                
                nota_completa = f"{entity} (Lembrete para {lembrete_time.strftime('%d/%m/%Y %H:%M')})"
                
                adicionar_nota(user_id, nota_completa, lembrete_time_aware)
                
            except Exception as e:
                logging.error(f"Erro ao agendar lembrete: {e}. Raw data: {raw_data}")
                await update.message.reply_text(f"Tentei agendar, mas falhei. A IA formatou a data/hora errado. (Erro: {e})")

        elif intent == "CONSULTAR_NOTAS":
            now_aware = datetime.now(SAO_PAULO_TZ)
            
            pendentes = consultar_notas_pendentes(user_id, now_aware)
            concluidas = consultar_notas_concluidas(user_id, now_aware)
            simples = consultar_notas_simples(user_id)
            
            if not pendentes and not concluidas and not simples:
                await update.message.reply_text("Você não possui registros.")
                return

            resposta = ""
            
            if pendentes:
                resposta += "⏰ **LEMBRETES PENDENTES:**\n"
                for (note_id, content) in pendentes:
                    resposta += f"  **ID {note_id}**: {content}\n"
                resposta += "\n"
            
            if concluidas:
                resposta += "✅ **LEMBRETES CONCLUÍDOS:**\n"
                for (note_id, content) in concluidas:
                    resposta += f"  **ID {note_id}**: {content}\n"
                resposta += "\n"

            if simples:
                resposta += "📝 **NOTAS SIMPLES:**\n"
                for (note_id, content) in simples:
                    resposta += f"  **ID {note_id}**: {content}\n"

            await update.message.reply_text(resposta)
        
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

    except Exception as e:
        logging.error(f"Erro CRÍTICO ao processar mensagem: {e}")
        await update.message.reply_text("Erro no processamento. Tente novamente.")

async def enviar_lembrete(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    mensagem = job.data
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 ALERTA, BRENO:\n\n- {mensagem}")

def main() -> None:
    setup_database()

    persistence = PicklePersistence(filepath="orion_lembretes_persistentes.pkl")
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Orion v2.2 está online. Pressione Ctrl+C para desligar.")
    application.run_polling()


if __name__ == '__main__':
    main()