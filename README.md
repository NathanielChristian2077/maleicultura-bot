# Maleicultura-bot

Bot de atendimento no WhatsApp para orientação em maleicultura. O backend utiliza o fluxo GPT-5-RAG como única rota de geração de respostas.

## 1. Requisitos

- Linux ou WSL2
- Python 3.12
- Docker
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

## 4. Prompt do sistema

O prompt do sistema deve existir em uma única fonte: `SYSTEM_PROMPT` em `src/config.py`.

Não adicione instruções comportamentais, prompts auxiliares ou cópias do prompt em outros módulos. O fluxo principal carrega o prompt por `services.memory.build_context_block()` e o envia como `instructions` em `services.llm.handler_gpt5_rag()`.

A sumarização automática de memória está desativada para evitar um segundo caminho de instrução para o modelo.

## 5. RAG documental

Esta branch usa apenas o fluxo GPT-5-RAG. O corpus de chunks fica em `data/chunks_out.jsonl`, importado do projeto `simple-rag-agents`. Gemini, DeepSeek, menus interativos e a pipeline de CSV do projeto original não são usados pelo bot.

As principais variáveis de RAG são configuradas em `template.yaml`:

- `RAG_EMBED_MODEL`: modelo de embeddings da OpenAI, por padrão `text-embedding-3-small`
- `RAG_CHROMA_COLLECTION`: coleção do Chroma, por padrão `chunks`
- `RAG_CHROMA_PATH`: caminho do banco Chroma dentro da imagem Lambda, por padrão `chroma_db`
- `RAG_TOP_K`: quantidade de trechos recuperados por pergunta, por padrão `3`

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

O diretório `src/chroma_db` é ignorado pelo Git para evitar versionar artefatos grandes, mas deve existir localmente antes de `sam build`. A imagem Lambda empacota esse diretório e, em runtime, copia o banco para `/tmp/chroma_db`, pois o filesystem da imagem é somente leitura fora de `/tmp`.

## 6. Arquitetura em produção

A aplicação usa duas Lambdas em imagem de container:

- `ApiFunction`: recebe o webhook HTTP do WhatsApp, deduplica o `wamid`, despacha o trabalho para a worker e responde rapidamente para evitar retry da Meta.
- `WorkerFunction`: executa o fluxo pesado GPT-5-RAG, mantém o indicador de digitação ativo enquanto processa e envia a resposta final pelo WhatsApp.

O DynamoDB `conversations` armazena histórico de conversa e registros temporários de deduplicação.

## 7. Deploy

Como o pacote com Chroma e dependências excede o limite de ZIP da Lambda, o deploy usa container image e ECR.

```bash
sam build
sam deploy \
  --stack-name maleicultura-bot \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --resolve-image-repos
```

## 8. Logs

Para acompanhar a Lambda do webhook:

```bash
API_FUNCTION_NAME="$(aws cloudformation describe-stack-resource \
  --stack-name maleicultura-bot \
  --logical-resource-id ApiFunction \
  --region us-east-1 \
  --query 'StackResourceDetail.PhysicalResourceId' \
  --output text)"

aws logs tail "/aws/lambda/$API_FUNCTION_NAME" \
  --since 10m \
  --region us-east-1 \
  --follow
```

Para acompanhar a Lambda worker:

```bash
WORKER_NAME="$(aws cloudformation describe-stack-resource \
  --stack-name maleicultura-bot \
  --logical-resource-id WorkerFunction \
  --region us-east-1 \
  --query 'StackResourceDetail.PhysicalResourceId' \
  --output text)"

aws logs tail "/aws/lambda/$WORKER_NAME" \
  --since 10m \
  --region us-east-1 \
  --follow
```
