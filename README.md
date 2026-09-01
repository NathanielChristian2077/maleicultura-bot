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

O identificador do modelo é definido pela variável `GPT5_RAG_MODEL` no `template.yaml`. A implementação da recuperação documental pode acrescentar os parâmetros próprios de RAG quando for integrada.

## 4. Deploy

```bash
sam build
sam deploy
```

Para acompanhar os logs:

```bash
sam logs -n ApiFunction --stack-name maleicultura-bot --tail
```
