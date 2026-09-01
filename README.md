# Maleicultura-bot

Bot de atendimento no WhatsApp para orientação em maleicultura. O backend utiliza um fluxo RAG adaptativo: mensagens sociais são respondidas sem LLM, perguntas técnicas comuns usam recuperação documental com modelo rápido e perguntas complexas podem usar o modelo principal.

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

Os identificadores dos modelos são definidos pelas variáveis `GPT5_RAG_MODEL` e `GPT5_FAST_MODEL` no `template.yaml`.

## 4. Prompt do sistema

O prompt do sistema deve existir em uma única fonte: `SYSTEM_PROMPT` em `src/config.py`.

Não adicione cópias do prompt em outros módulos. As regras específicas de grounding documental ficam em `src/rag/prompt.py`, enquanto a apresentação determinística das fontes fica em `src/rag/citations.py`.

A sumarização automática de memória permanece desativada. O histórico só é carregado para rotas que dependem do contexto anterior.

## 5. RAG documental

O corpus de chunks fica em `data/chunks_out.jsonl`, importado do projeto `simple-rag-agents`. A ingestão limpa duplicações consecutivas de OCR, normaliza o texto, acrescenta contexto de documento/página e cria dois índices locais:

- Chroma para recuperação vetorial;
- SQLite FTS5 (`lexical.sqlite3`) para recuperação lexical.

Em runtime, os candidatos dos dois mecanismos são fundidos e passam por um gate de relevância antes de os melhores trechos serem enviados ao modelo.

As principais variáveis de RAG são configuradas em `template.yaml`:

- `RAG_EMBED_MODEL`: modelo de embeddings da OpenAI, por padrão `text-embedding-3-small`;
- `RAG_CHROMA_COLLECTION`: coleção do Chroma, por padrão `chunks`;
- `RAG_CHROMA_PATH`: diretório dos índices dentro da imagem worker, por padrão `chroma_db`;
- `RAG_TOP_K`: quantidade final de trechos enviados ao modelo, por padrão `3`;
- `RAG_DENSE_K`: candidatos da busca vetorial, por padrão `12`;
- `RAG_LEXICAL_K`: candidatos da busca lexical, por padrão `12`;
- `RAG_MIN_RELEVANCE`: limiar mínimo usado pelo gate de relevância, por padrão `0.18`.

### Recriação obrigatória do índice

Após mudanças em ingestão, metadados ou recuperação, recrie o banco antes do build. Esta etapa também gera `lexical.sqlite3`:

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

O diretório `src/chroma_db` é ignorado pelo Git, mas deve existir antes de `sam build`. Em runtime a worker copia esse diretório para `/tmp/chroma_db`, pois o filesystem da imagem é somente leitura fora de `/tmp`.

## 6. Fluxo adaptativo

O roteador em `src/services/router.py` classifica a mensagem antes de pagar pelo pipeline completo:

- saudações e agradecimentos: resposta estática, sem DynamoDB, embedding ou LLM;
- pergunta técnica direta: RAG com modelo rápido e orçamento curto de saída;
- continuação curta: histórico recente é usado para enriquecer a consulta de recuperação;
- pergunta complexa: histórico e modelo principal podem ser utilizados.

Respostas técnicas recebem fontes determinísticas ao final da mensagem. Se o modelo citar `[1]` ou `[2]`, apenas as fontes utilizadas são mostradas; se ele omitir os marcadores, o backend acrescenta os documentos recuperados para que a resposta nunca esconda a proveniência disponível.

## 7. Arquitetura em produção

A aplicação usa duas Lambdas em imagens separadas:

- `ApiFunction`: imagem leve, sem OpenAI, LangChain ou Chroma; recebe o webhook, deduplica o `wamid`, despacha a worker e retorna rapidamente;
- `WorkerFunction`: contém o pipeline RAG, o índice documental, mantém o indicador de digitação e envia a resposta final.

O DynamoDB `conversations` armazena histórico e registros temporários de deduplicação. A resposta do WhatsApp é enviada antes da persistência do diálogo; usuário e assistente são gravados juntos com `BatchWriteItem` depois do envio bem-sucedido.

## 8. Testes

A suíte unitária não exige chamadas reais à OpenAI ou AWS:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Ela cobre roteamento adaptativo, reescrita de continuação, limpeza de corpus, índice FTS5, fusão dense/lexical, gate de relevância, fontes determinísticas, persistência em lote e separação das imagens de container.

O workflow `.github/workflows/unit-tests.yml` executa a mesma validação em pushes da branch de implementação e em pull requests.

## 9. Deploy

As dependências da API e da worker são separadas em `src/requirements-api.txt` e `src/requirements-worker.txt`. O SAM usa `Dockerfile.api` e `Dockerfile.worker` respectivamente.

```bash
sam build
sam deploy \
  --stack-name maleicultura-bot \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --resolve-image-repos
```

## 10. Logs

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
