import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from mangum import Mangum

import boto3
from botocore.config import Config
import asyncio
import re
import time

app = FastAPI()
_lambda = boto3.client("lambda", config=Config(retries={"max_attempts": 2}))

# Parece que ainda existem situações onde acontece de uma mensagem receber mais de uma resposta do llm, mais frequente com o gemini (não consegui pensar num motivo) 
_seen_wamids: dict[str, float] = {} 
_DEDUP_TTL_SEC = 600

def seen_bfr(wamid: str):
    now = time.time()
    expired = [
        k for k, ts in _seen_wamids.items() 
            if now - ts > _DEDUP_TTL_SEC
    ]

    for k in expired:
        _seen_wamids.pop(k, None)
    if not wamid:
        return False
    if wamid in _seen_wamids:
        return True
    _seen_wamids[wamid] = now
    return False

def env(name: str, default: str = "") -> str:
    fallback_map = {
        "WHATSAPP_VERIFY_TOKEN": ["VERIFY_TOKEN"],
        "WHATSAPP_TOKEN": ["WABA_TOKEN"],
        "WHATSAPP_PHONE_NUMBER_ID": ["PHONE_NUMBER_ID"],
        "GRAPH_API_VERSION": ["GRAPH_VERSION"],
        "DRY_RUN": [],
    }
    val = os.getenv(name)
    if val is None:
        for fb in fallback_map.get(name, []):
            val = os.getenv(fb)
            if val is not None:
                break
    return val if val is not None else default

def cfg():
    raw = env("WHATSAPP_TOKEN")
    clean = (raw or "").replace("\r", "").replace("\n", "").strip().strip('"').strip("'")
    return {
        "VERIFY_TOKEN": env("WHATSAPP_VERIFY_TOKEN"),
        "WABA_TOKEN": clean,
        "PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"),
        "GRAPH_VERSION": env("GRAPH_API_VERSION", "v20.0"),
        "DRY_RUN": env("DRY_RUN", "false").lower() == "true",
    }

@app.get("/webhook")
async def verify(request: Request):
    C = cfg()
    qp = request.query_params

    mode = qp.get("hub.mode") or qp.get("mode")
    verify_token = qp.get("hub.verify_token") or qp.get("verify_token")
    challenge = qp.get("hub.challenge") or qp.get("challenge")

    if mode == "subscribe" and verify_token == C["VERIFY_TOKEN"] and challenge:
        return PlainTextResponse(challenge)

    return Response(status_code=403)

@app.post("/webhook")
async def incoming(request: Request):
    C = cfg()
    try:
        raw = await request.body()
        print({"type": "wa_inbound_raw", "len": len(raw)})

        body = await request.json()
        print({"type": "wa_inbound_parsed", "keys": list(body.keys())})

        entry = (body.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value  = change.get("value") or {}

        if not value.get("messages"):
            print({"type": "wa_inbound_skip", "reason": "no_messages_key"})
            return {"status": "ok"}

        msg = value["messages"][0]
        wa_type = msg.get("type")
        wa_from = msg.get("from")
        is_echo = msg.get("from") == (value.get("metadata") or {}).get("display_phone_number")
        if is_echo:
            print({"type":"wa_inbound_skip", "reason":"echo"})
            return {"status": "ok"}
        
        wamid = msg.get("id") or msg.get("wamid")
        if seen_bfr(wamid):
            print({"type":"wa_inbound_skip", "reason":"duplicate_wamid", "wamid": wamid})
            return {"status":"ok"}

        print({"type":"wa_inbound_message", "from": wa_from, "msg_type": wa_type})

        url = f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/message"
        
        token = (C["WABA_TOKEN"] or "")
        token = token.replace("\r", "").replace("\n", "").strip().strip('"').strip("'")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Feedback imediato pro usuário
        try:
            async with httpx.AsyncClient(timeout = 10) as cliente_fb:
                # Marca mensagem como lida
                await cliente_fb.post(
                    url,
                    headers,
                    json = {
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": wamid,
                    },
                )

                # Faz a animação de typing na conversa e define o contato como typing na visualização das conversas
                await cliente_fb.post(
                    url,
                    headers,
                    json = {
                       "messaging_product": "whatsapp",
                       "to": wa_from,
                       "type": "typing",
                       "typing": {"status": "typing"}, 
                    },
                )
        except Exception as e:
            print({"type": "wa_feedback_err", "error": str(e)})

        #=============================================================================
        #               Handlers a serem usados para integrar as APIs
        #=============================================================================
        #   Métodos, imports ou quaisquer implementações devem ser feitas apenas dentro do handler designado.
        #   Se precisarem de qualquer ajuda com relação a tokens ou variáveis de ambiente (provavelmente vão dar problema por causa do AWS), me mandem mensagem que eu descubro o problema.
        #   No README.md eu detalho melhor como fazer o deploy, é fácil, mas o ideal é fazer os comandos pelo Linux (ou wsl), na verdade eu não tenho ideia de como funciona direto no Windows.
        
        # prefixo: @
        async def dev_handler1(user_text:str, wa_from: str) -> str:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain.prompts import ChatPromptTemplate

            llm = ChatGoogleGenerativeAI(
                model= 'gemini-2.5-flash',
                google_api_key= env("GEMINI_API_KEY"),
                convert_system_message_to_human=True
            )

            prompt = ChatPromptTemplate.from_messages([
                ("system","""
                        Você é um consultor agrícola especializado em pomares de maçã, com foco em ajudar produtores rurais.
                        Sua missão é explicar de forma simples, prática e acessível, evitando termos muito técnicos ou acadêmicos.

                        Sempre que possível:
                        - Dê exemplos reais de manejo.
                        - Sugira passos práticos que o produtor possa aplicar no dia a dia.
                        - Traga dicas sobre plantio, poda, irrigação, adubação, pragas, colheita e venda de maçãs.
                        - Use uma linguagem de conversa amigável, como se estivesse no campo com o produtor.

                        Se o usuário fizer perguntas fora da maleicultura, responda de forma breve e procure trazer o foco de volta para a cultura da maçã.
                        O tom deve ser acolhedor e confiante, mostrando experiência, mas sem parecer complicado demais.
                    """),
                ("user", "{user}")
            ])

            chain = prompt | llm

            response = await asyncio.to_thread(chain.invoke, {"user":user_text})

            try:
                return response['text']
            except Exception:
                return (getattr(response, "content", None) or str(response) or "").strip()
    
        # prefixo: $
        async def dev_handler2(user_text:str, wa_from: str) -> str:
            import openai

            # Tive que dar uma alterada em como estava setado o gpt-5, estavam vindo muitos erros da api, aparentemente por ser com o uso do gpt-5 algumas coisas mudam, na prática não mudou nada
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
            try:
                response = await asyncio.to_thread(
                    lambda: client.responses.create(
                        model="gpt-5-2025-08-07",
                        reasoning={"effort":"minimal"},
                        instructions="Você é um assistente de maleicultores brasileiros extremamente prestativo e objetivo que fala apenas em português.",
                        input=user_text 
                    )
                )
                assistant_message = response.output_text
                
                return assistant_message.strip() if assistant_message is not None else "Desculpe, ocorreu um erro ao processar sua solicitação."
            except Exception as e:
                print({"type":"openai_exception", "error": str(e)})
                return "Desculpe, ocorreu um erro ao processar sua solicitação."
    
        # prefixo: &
        async def dev_handler3(user_text:str, wa_from: str) -> str:
            from langchain_openai import ChatOpenAI
            from langcahin.schema import SystemMessage, HumanMessage, AIMessage
            from botocore.exceptions import ClientError

            TABLE = os.getenv("CONV_TABLE", "conversations")
            ddb = boto3.client("dynamoDB")

            def _ts_ms() -> int:
                return int(time.time() * 1000)
            
            # Salva a mensagem, ts(time stamp), role e o número(wa_from -> PHONE_NUMBER_ID) do usuário
            def save_message(role: str, body: str) -> None:
                try:
                    ddb.put_item(
                        TableName = TABLE,
                        Item = {
                            "wa_from": {"S": wa_from},
                            "ts": {"N": str(_ts_ms())},
                            "role": {"S": role},
                            "content": {"S": body or ""},
                        },
                    )
                except ClientError  as e:
                    print({"type": "ddb_put_err", "error": str(e)})
            
            # Retorna apenas as mensagens do usuário de id = :w
            # Isso é feito sem intervenção do llm, direcionando o foco do mesmo apenas para o conteúdo das mensagens
            # Esse trabalho fica pra mais uma das funcionalidades do aws, esse é o DynamoDB, trata-se de um NoSQL
            def fetch_last(limit: int = 10):
                try:
                    resp = ddb.query(
                        TableName = TABLE,
                        KeyConditionExpression = "wa_from = :w",
                        ExpressionAttributeValues = {":w": {"S": wa_from}},
                        Limit = limit,
                        ScanIndexForward = False, # -> da mais recente pra mais antiga
                    )

                    items = resp.get("Items", [])
                    # e aqui retorna em ordem cronológica
                    items = list(reversed(items))
                    out = []
                    
                    for it in items:
                        out.append({
                            "role": it["role"]["S"],
                            "body": it["body"]["S"],
                        })

                    return out
                except ClientError as e:
                    print({"type": "ddb_query_err", "error": str(e)})
                    return []
            
            save_message("user", user_text) # do usuário

            history = fetch_last() # Carrega as últimas 10 mensagens(ajustável)
                                   # Tenha em mente que nessas 10 mensagens estão incluídas as mensagens do LLM, IDEALMENTE serão 50/50 entre user e assistant 
            
            # montagem do request. Mudou um pouco por causa do langchain, mas a ideia ainda é a mesma
            msgs = [SystemMessage(content="Você é um assistente de produtores de maçã brasileiros extremamente prestativo e objetivo que fala apenas em português.")]

            for reg in history:
                r = reg.get("role", "").lower()
                b = reg.get("body", "")
                if r == "user":
                    msgs.append(HumanMessage(content=b))
                elif r == "assistant":
                    msgs.append(AIMessage(content=b))
            if not msgs or not isinstance(msgs[-1], HumanMessage):
                msgs.append(HumanMessage(content=user_text))

            client = ChatOpenAI(
                api_key=os.environ.get('DEEPSEEK_API_KEY'),
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                temperature=0.3,
                max_tokens=300 # 300 tokens = +/- 300/0.3 = 1000 caracteres, na prática seria um pouco mais, mas considerando apenas testes...
            )
            try:
                response = await asyncio.to_thread(client.invoke, msgs)
                send = (getattr(response, "body", None) or str(response)).strip()
            except Exception as e:
                print({"type": "deepseek_exception", "error": str(e)})
                send = None # Aqui poderia ser a mensagem de fallback pedindo uma nova tentativa, mas como isso ainda é um teste individual, mantive ela no 'reply_text'
            # Salvando a mensagem do próprio llm, relembrando que ela será uma das 10 mensagens salvas no fetch_class()
            save_message("assistant", send)

            return send
        
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            text = ""

        prefix = text[:1]
        user_text = text[1:].lstrip() if len(text) > 1 else ""
        match prefix:
            case "@":
                reply_text = await dev_handler1(user_text=user_text, wa_from=wa_from)
            case "$":
                reply_text = await dev_handler2(user_text=user_text, wa_from=wa_from)
            case "&":
                reply_text = await dev_handler3(user_text=user_text, wa_from=wa_from)
            case _:
                reply_text = (
                    "Prefixo de mensagem não definido, por favor use:\n"
                    "@ [texto] -> Fabricio\n"
                    "$ [texto] -> Bruno\n"
                    "& [texto] -> Nathaniel\n"
                )
         
        if C["DRY_RUN"]:
            print({"type":"wa_outbound_dry_run", "to": wa_from, "text": reply_text[:4096]})
            return {"status": "dry_ok"}

        reply_text = (reply_text or "Parece que algo deu errado, tente novamente por gentileza.")
        # O Fabricio relatou que tiveram casos onde as respostas dos llms estavam chagando com markdown, então fiz isso aqui pra limpar.
        # Não tenho certeza, mas acho que dá pra setar algum parâmetro em pra resposta vir como "plaintext", se encontrarem, adicionem pros merges futuros e me avisem
        reply_text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", reply_text)
        reply_text = re.sub(r"[`*_~#>]", "", reply_text)
        reply_text = reply_text.strip()
        if len(reply_text) > 4096:
            reply_text = reply_text[:4095] + "…"

        # Interrompe o status: typing antes de enviar a resposta do LLM
        try:
           async with httpx.AsyncClient(timeout = 10) as client_fb2:
               await client_fb2.post(
                   url,
                   headers,
                   json = {
                       "messaging_product": "whatsapp",
                       "to": wa_from,
                       "type": "typing",
                       "typing": {"status": "paused"},
                   },
               ) 
        except Exception as e:
            print({"type": "wa_feedback_err_pause", "error": str(e)})

        payload = {
            "messaging_product": "whatsapp",
            "to": wa_from,
            "type": "text",
            "text": {"body": reply_text}, # o limite do whatsapp é 4096
        }
        
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)

        txt = resp.text[:4000] # o limite do whatsapp é 4096
        print({"type":"wa_outbound_resp", "status": resp.status_code, "body": txt})

        if resp.is_success:
            return {"status": "sent"}

        return {"status": "error", "code": resp.status_code}

    except Exception as e:
        print({"type":"wa_exception", "error": str(e)})
        return {"status": "exception"}

handler = Mangum(app)
