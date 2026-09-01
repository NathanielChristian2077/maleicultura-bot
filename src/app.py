import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from mangum import Mangum

import boto3
from botocore.config import Config

app = FastAPI()
_lambda = boto3.client("lambda", config=Config(retries={"max_attempts": 2}))

def env(name: str, default: str = "") -> str:
    fallback_map = {
        "WHATSAPP_VERIFY_TOKEN": ["VERIFY_TOKEN"],
        "WHATSAPP_TOKEN": ["WABA_TOKEN"],
        "WHATSAPP_PHONE_NUMBER_ID": ["PHONE_NUMBER_ID"],
        "GRAPH_API_VERSION": ["GRAPH_VERSION"],
        "DRY_RUN": [],
        "GEMINI_API_KEY": [""]
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

        print({"type":"wa_inbound_message", "from": wa_from, "msg_type": wa_type})
        
        #=============================================================================
        #               Handlers a serem usados para integrar as APIs
        #=============================================================================
        #   Métodos, imports ou quaisquer implementações devem ser feitas apenas dentro do handler designado.
        #   Se precisarem de qualquer ajuda com relação a tokens ou variáveis de ambiente (provavelmente vão dar problema por causa do AWS), me mandem mensagem que eu descubro o problema.
        #   No README.md eu detalho melhor como fazer o deploy, é fácil, mas o ideal é fazer os comandos pelo Linux (ou wsl), na verdade eu não tenho ideia de como funciona direto no Windows.
        
        # prefixo: @
        async def dev_handler1(user_text:str, wa_from: str, C: dict) -> str:
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

            chain = llm | prompt

            response = chain.invoke({"user":user_text})

            return response['text']
    
        # prefixo: $
        async def dev_handler2(user_text:str, wa_from: str, C: dict) -> str:
            import openai
            # Conferir e setar a chave da API depois
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
            try:
                response = client.chat.completions.create(
                    model="gpt-5",
                    messages=[
                        {"role": "system", "content": "Você é um assistente de maleicultores brasileiros extremamente prestativo e objetivo que fala apenas em português."},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=0.3,
                    max_tokens=300, 
                )
                assistant_message = response.choices[0].message.content
                return assistant_message.strip() if assistant_message is not None else "Desculpe, ocorreu um erro ao processar sua solicitação."
            except Exception as e:
                print({"type":"openai_exception", "error": str(e)})
                return "Desculpe, ocorreu um erro ao processar sua solicitação."
    
        # prefixo: &
        async def dev_handler3(user_text:str, wa_from: str, C: dict) -> str:
            #TODO: Integrar LLM (Nathaniel)
            import openai
            from langchain import memorys
            from langchain.chains import ConversationChain
            # Apenas para teste, a API oficial será usada posteriormente para produção
            client = openai.OpenAI(
                base_url="https://api.llm7.io/v1/chat/completions",
                api_key="V9aON2wRd+sr0kjbRRp+ZsxIEkZ0VIs4rFUcuWg+YqCtgaMFQ4UFExXFdPTn/Jxj4+BWxjWurv3I9mxWflu7430gGIFfhVnUYvD2hFKPNEbf/ZKj8Ujqw68soVakuAP4ImZdz6ZPww==",
                http_client=httpx.Client(verify=False)
            )
            response = client.chat.completions.create(
                model="deepseek-v3.1",
                messages=[
                    {"role": "system", "content": "Você é um assistente de maleicultores brasileiros extremamente prestativo e objetivo que fala apenas em português."},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.3,
                # 300 tokens = +/- 300/0.3 = 1000 palavras, na prática seria um pouco mais, mas considerando apenas testes...
                max_tokens=300, # Ajustar para 500 {500/0.27 = 1851 palavras} em produção.
                max_retries=2,
                request_timeout=15
            )
            return response.choices[0].message.content.strip()
        
        text = (msg.get("text") or {}).get("body", "").strip()
        if not text:
            text = ""

        prefix = text[:1]
        user_text = text[1:].lstrip() if len(text) > 1 else ""
        match prefix:
            case "@":
                reply_text = await dev_handler1(user_text=user_text, wa_from=wa_from, C=C)
            case "$":
                reply_text = await dev_handler2(user_text=user_text, wa_from=wa_from, C=C)
            case "&":
                reply_text = await dev_handler3(user_text=user_text, wa_from=wa_from, C=C)
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

        url = f"https://graph.facebook.com/{C['GRAPH_VERSION']}/{C['PHONE_NUMBER_ID']}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": wa_from,
            "type": "text",
            "text": {"body": str(reply_text)[:4096]}, # o limite do whatsapp é 4096
        }
        token = (C["WABA_TOKEN"] or "")
        token = token.replace("\r", "").replace("\n", "").strip().strip('"').strip("'")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
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
