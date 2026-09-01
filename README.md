# Maleicultura-bot

> - Se alguém souber e quiser deixar esse README bonitinho, sinta-se à vontade, eu não sei, então só inclui o necessário.
> - Outra coisa, a resposta padrão agora é para ter deixado de ser "oi", podem encontrar o atual modelo dela em app.py como "case _: [...]", ainda não testei nada
> - Finalmente, caso o git reclame que tem CRLF com LF misturado, setem o autocrlf para false, tem vários jeitos de fazer, imagino que não terão problema com isso, só estou avisando porque tive que dedicar algumas linhas de código apenas para isso, talvez seja necessário fazer algo semelhante para as chaves das APIs, existe no método 'cfg()' (app.py) uma variável 'clean', podem usar algo semelhante para evitar problemas do 'sam'.

## 1. Base

- OS: Linux/WSL2
- Python3
- AWS CLI
- SAM CLI

### 1.2 - AWS CLI - Instalação

```bash
    cd /temp # (Opicional, recomendação)
    curl -fsSLo awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
    unzip -q awscliv2.zip
    sudo ./aws/install -i /usr/local/aws -b /usr/local/bin
    aws --version # Só pra ter certeza que tudo funcionou
```

### 1.3 - SAM CLI - Instalação

```bash
    python3 -m pip install --user pipx # (Tentem com pip install pipx se estiverem no próprio pc)
    python3 -m pipx ensurepath
    source ~/.profile
    pipx install aws-sam-cli
    sam --version
```

## 2. Configurando Credenciais AWS

No WSL/linux bash:

```bash
    aws configure
    # AWS Access Key ID: [...]
    # AWS Secret Access Key: [...]
    # Default region name: us-east-1 (mais barato e é onde nosso projeto está, então não pode ser diferente)
    # Default output format: json (OBS.: Não tenho certeza se vocês terão acesso a essas duas últimas opções, caso não, apenas ignorem)
```

### **v NÃO PERCAM v**

- #### **Fabricio:**

    **AccessKeyId**: AKIA4XZA2XBNEI726CNA
    **SecretAccessKey**: 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

- #### **Bruno:**

    **AccessKeyId**: AKIA4XZA2XBNMXYKP7J2
    **SecretAccessKey**: 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

### **^ SÃO ÚNICAS ^**

Teste:

```bash
    aws sts get-caller-identity
```

## 3. Parâmetros no SSM

Provavelmente vai ser necessário adicionar novos parâmetros para chave da API de cada AI, no caso de ser necessário, me contatem que eu faço o mais rápido possível, do contrário existem instruções a seguir do que fazer ou nesse caso, não fazer.

```bash
    # A ideia é que ninguém precise mudar isso nunca, então pulem essa parte.
    # É só um registro caso algo se perca. 
    # Então vou inserir como um comentário ao lado de cada parâmetro o valor que deve ir no lugar dos placeholders.
    aws ssm put-parameter --name /maleicultura/whatsapp_verify_token --type String --value "PLACEHOLDER_VERIFY" --overwrite # apichatbotteste
    aws ssm put-parameter --name /maleicultura/whatsapp_token --type String --value "PLACEHOLDER_TOKEN" --overwrite # EAALHFOBiiNMBPQga8cGPJCXJ1gCeTWyTfAZCIZAdFBz8XokbUEYlJofzalIy9nOw9VFZAHFyKJSrzkuL2ssJoq9xh585r8JGUQEQZBNqLNiqVUScNJpRbZBwNdZBp1D65uV40FHgZBdIdBkzEe3uopalaRe7zwrexkY3SZBMFZBKuap7okR2BBWj9mY43tprhHZC46hgZDZD
    aws ssm put-parameter --name /maleicultura/phone_number_id --type String --value "00000000000000000" --overwrite # 829474636907437
```

## 4. Deploy

Esses são os principais comandos a se utilizar, eles são bem autoexplicativos, façam as implementações, usem os comandos de deploy e com sorte nada vai quebrar.

```bash
sam build
sam deploy

# Para ver os logs:
sam logs -n ApiFunction --stack-name maleicultura-bot --tail # Está em UTC+0 (caso os horários e/ou datas pareçam estranhos)
# Só use o comando a seguir caso seja muito necessário, ou você estiver plenamente convicto do que está fazendo:
sam build --guided
```
