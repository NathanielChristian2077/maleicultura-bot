# Maleicultura-bot

Bot de atendimento no WhatsApp para orientação em maleicultura. O backend utiliza o fluxo GPT-5-RAG como única rota de geração de respostas.

## 1. Requisitos

- Linux ou WSL2
- Python 3.12
- AWS CLI
- SAM CLI

### AWS CLI

```bash
cd /tmp
curl -fsSLo awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
unzip -q awscliv2.zip
sudo ./aws/install -i /usr/local/aws -b /usr/local/bin
aws --version
```

### SAM CLI

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
source ~/.profile
pipx install aws-sam-cli
sam --version
```

## 2. Credenciais AWS

Configure credenciais AWS individuais, com o mínimo de permissões necessário para o ambiente de desenvolvimento ou implantação.

```bash
aws configure
aws sts get-caller-identity
```

Nunca versione chaves de acesso, tokens de WhatsApp ou chaves de API.

## 3. Parâmetros no SSM

O template utiliza os seguintes parâmetros no AWS Systems Manager Parameter Store:

- `/maleicultura/whatsapp_verify_token`
- `/maleicultura/whatsapp_token`
- `/maleicultura/phone_number_id`
- `/maleicultura/openai_api_key`

O identificador do modelo é definido pela variável `GPT5_RAG_MODEL` no `template.yaml`.

## 4. RAG documental

Esta branch usa apenas o fluxo GPT-5-RAG. O corpus de chunks fica em `data/chunks_out.jsonl`, importado do projeto `simple-rag-agents`. Gemini, DeepSeek e a pipeline de CSV do projeto original não são usados pelo bot.

As principais variáveis de RAG são configuradas em `template.yaml`:

- `RAG_EMBED_MODEL`: modelo de embeddings da OpenAI, por padrão `text-embedding-3-small`
- `RAG_CHROMA_COLLECTION`: coleção do Chroma, por padrão `chunks`
- `RAG_CHROMA_PATH`: caminho do banco Chroma dentro do pacote Lambda, por padrão `chroma_db`
- `RAG_TOP_K`: quantidade de trechos recuperados por pergunta, por padrão `5`

Antes de empacotar/deployar, gere o banco vetorial localmente. O comando abaixo lê `data/chunks_out.jsonl` e cria `src/chroma_db`:

```bash
pip install -r src/requirements.txt
cd src
python -m rag.ingest create
cd ..
```

Para acrescentar novos chunks sem recriar tudo:

```bash
cd src
python -m rag.ingest append
cd ..
```

O diretório `src/chroma_db` é ignorado pelo Git para evitar versionar artefatos grandes, mas deve existir localmente antes de `sam build` caso o banco seja empacotado junto da Lambda. Se `RAG_CHROMA_PATH` apontar para outro local, como uma Layer montada em `/opt`, ajuste a variável no template ou no ambiente de deploy.

## 5. Deploy

```bash
sam build
sam deploy
```

Para acompanhar os logs:

```bash
sam logs -n ApiFunction --stack-name maleicultura-bot --tail
```
