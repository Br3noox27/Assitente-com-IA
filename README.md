# Orion — Assistente Pessoal com IA via Telegram

> Bot pessoal em Python que entende comandos por texto ou áudio, usando a API do Gemini para interpretar intenção e executar ações (lembretes, notas, consultas) sem depender de comandos fixos.

## Contexto

Construído para resolver um problema pessoal de produtividade: listas de tarefas passivas não geram ação. O Orion notifica proativamente (push) em vez de depender de eu abrir um app e checar.

## Como funciona

1. Usuário manda mensagem de texto ou áudio no Telegram
2. Para áudio: o arquivo é enviado direto para a API multimodal do Gemini (sem transcrição separada)
3. O Gemini interpreta a intenção e responde em linguagem natural + emite um comando estruturado (ex: `[AGENDAR_LEMBRETE: "assunto", "data"]`)
4. O bot faz parsing desse comando via regex e executa a ação correspondente (salvar nota, agendar lembrete, consultar registros, deletar)
5. Lembretes agendados disparam notificação push automática no horário definido

## Arquitetura

**Bot:** python-telegram-bot (Application, CommandHandler, MessageHandler)
**IA:** Google Generative AI (Gemini 2.5 Pro) via API direta
**Persistência:** SQLite (notas/lembretes) + PicklePersistence (estado de jobs agendados)
**Agendamento:** JobQueue assíncrono com tratamento de fuso horário (pytz, America/Sao_Paulo)

## Desafios e decisões técnicas

**Function calling manual:** em vez de usar um framework de orquestração, o prompt define uma "caixa de ferramentas" e instrui o modelo a emitir comandos estruturados em uma linha separada da resposta em linguagem natural. O bot faz parsing desse padrão com regex.

**Mitigação de alucinação:** versões iniciais do prompt levavam o modelo a "conversar" em vez de executar ações quando o comando deveria ser direto. O prompt foi reescrito para priorizar explicitamente o uso de ferramentas sobre conversação livre.

**Multimodalidade:** áudio é enviado diretamente ao Gemini sem etapa de transcrição separada, simplificando o pipeline.

## Limitações conhecidas

- Parsing de argumentos do comando (`raw_data.split('", "')`) é sensível a aspas dentro do próprio conteúdo da nota
- Persistência em SQLite local, sem deploy em produção
- Sem testes automatizados

## Stack

Python · python-telegram-bot · Google Generative AI (Gemini 2.5 Pro) · SQLite · pytz