Guia passo a passo: da VPS ao deploy completo. Verificando a estrutura atual do projeto:


[4 tools called]


# Guia: Deploy completo - VPS Hostinger + Supabase + Domínio

Guia passo a passo do zero ao deploy em produção.

## Arquitetura final

```
┌─────────────────────────────────────────┐
│           Domínio (marketdash.com.br)    │
│  ├─ marketdash.com.br (Frontend Prod)   │
│  ├─ api.marketdash.com.br (Backend Prod)│
│  ├─ marketdash.hml.com.br (Frontend HML)│
│  └─ api.marketdash.hml.com.br (Backend) │
└──────────────┬──────────────────────────┘
               │
        ┌──────▼──────┐
        │     VPS     │
        │  (Hostinger)│
        │             │
        │  ┌────────┐ │
        │  │ Nginx  │ │ (Reverse Proxy + SSL)
        │  └───┬────┘ │
        │      │      │
        │  ┌───▼────┐ │
        │  │FastAPI │ │ (Backend Docker)
        │  │:8000   │ │
        │  └────────┘ │
        │             │
        │  ┌────────┐ │
        │  │Frontend│ │ (React Build)
        │  │Static  │ │
        │  └────────┘ │
        └─────────────┘
               │
        ┌──────▼──────────┐
        │    Supabase     │ (Cloud - externo)
        │  • Auth         │
        │  • PostgreSQL   │
        │  • Storage      │
        └─────────────────┘
```

---

## Etapa 1: Comprar VPS e domínio

### 1.1. Comprar VPS Hostinger KVM 2
1. Acesse: https://www.hostinger.com.br/vps
2. Selecione: KVM 2 (2 vCPUs, 2GB RAM, 40GB SSD)
3. Sistema operacional: Ubuntu 22.04 LTS
4. Região: escolha próxima ao Brasil
5. Finalize a compra
6. Aguarde o e-mail com:
   - IP da VPS
   - Usuário root
   - Senha root

### 1.2. Comprar domínio
1. Na Hostinger: https://www.hostinger.com.br/dominios
2. Compre o domínio (ex: `marketdash.com.br`)
3. Configure os DNS (será feito na Etapa 3)

---

## Etapa 2: Configurar Supabase

### 2.1. Criar conta e projeto no Supabase
1. Acesse: https://supabase.com
2. Crie conta (ou login)
3. New Project
4. Preencha:
   - Name: `dashads-prod`
   - Database Password: (crie uma senha forte)
   - Region: escolha próxima ao Brasil
5. Aguarde a criação (1-2 minutos)

### 2.2. Criar projeto de homologação
1. Repita o passo 2.1 com:
   - Name: `dashads-staging`

### 2.3. Obter credenciais
Para cada projeto (prod e staging):

1. Settings → API
2. Anote:
   - Project URL: `https://xxxxx.supabase.co`
   - anon/public key: `eyJhbGci...`
   - service_role key: `eyJhbGci...` (mantenha secreta)

3. Settings → Database
4. Anote:
   - Connection string (URI): `postgresql://postgres:[PASSWORD]@db.xxxxx.supabase.co:5432/postgres`

### 2.4. Configurar autenticação
1. Authentication → Settings
2. Site URL: `https://marketdash.com.br` (produção)
3. Redirect URLs: adicione:
   - `https://marketdash.com.br/**`
   - `https://marketdash.hml.com.br/**`
   - `http://localhost:3000/**` (desenvolvimento)

---

## Etapa 3: Configurar domínio (DNS)

### 3.1. Configurar DNS na Hostinger
1. Login → Domínios → Gerenciar
2. DNS / Nameservers → Gerenciar DNS
3. Adicione registros A:

```
Tipo: A
Nome: @ (ou deixe em branco para o domínio raiz)
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: marketdash.com.br (frontend produção)

Tipo: A
Nome: api
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: api.marketdash.com.br (backend produção)

Tipo: A
Nome: @ (ou deixe em branco)
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: marketdash.hml.com.br (frontend homologação)

Tipo: A
Nome: api
Valor: [IP_DA_VPS]
TTL: 3600
Descrição: api.marketdash.hml.com.br (backend homologação)
```

**Nota**: Para os domínios de homologação (`marketdash.hml.com.br` e `api.marketdash.hml.com.br`), você precisará criar um subdomínio `hml` primeiro na Hostinger, ou configurar como domínio separado se `hml.com.br` for um domínio diferente.

Nota: propagação pode levar até 24h (geralmente 1-2h).

### 3.2. Verificar DNS
```bash
# No seu computador, execute:
nslookup api.marketdash.com.br
# Deve retornar o IP da VPS
```

---

## Etapa 4: Configurar VPS (primeira conexão)

### 4.1. Conectar via SSH
No Windows (PowerShell) ou Linux/Mac:

```bash
ssh root@[IP_DA_VPS]
# Digite a senha quando solicitado
```

### 4.2. Atualizar sistema
```bash
apt update && apt upgrade -y
```

### 4.3. Criar usuário não-root
```bash
# Criar usuário
adduser dashads
usermod -aG sudo dashads

# Configurar SSH para novo usuário
mkdir -p /home/dashads/.ssh
cp ~/.ssh/authorized_keys /home/dashads/.ssh/
chown -R dashads:dashads /home/dashads/.ssh
chmod 700 /home/dashads/.ssh
chmod 600 /home/dashads/.ssh/authorized_keys

# Sair e reconectar com novo usuário
exit
```

Reconecte:
```bash
ssh dashads@[IP_DA_VPS]
```

### 4.4. Instalar dependências
```bash
# Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Nginx
sudo apt install nginx -y

# Certbot (para SSL)
sudo apt install certbot python3-certbot-nginx -y

# Git
sudo apt install git -y

# Node.js (para build do frontend, se necessário)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Recarregar grupos (ou reconectar SSH)
newgrp docker
```

### 4.5. Verificar instalações
```bash
docker --version
docker-compose --version
nginx -v
certbot --version
node --version
```

---

## Etapa 5: Preparar backend para produção

### 5.1. Criar estrutura de diretórios na VPS
```bash
# Criar estrutura
sudo mkdir -p /var/www/dashads
sudo mkdir -p /var/www/dashads/backend
sudo mkdir -p /var/www/dashads/backend-staging
sudo mkdir -p /var/www/dashads/frontend
sudo mkdir -p /var/www/dashads/frontend-staging
sudo chown -R $USER:$USER /var/www/dashads
```

### 5.2. Criar Dockerfile de produção
Crie `Dockerfile.prod` no backend:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run with gunicorn for production
RUN pip install gunicorn[gevent]
CMD ["gunicorn", "app.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]
```

### 5.3. Criar docker-compose de produção
Crie `docker-compose.prod.yml`:

```yaml
version: '3.8'

services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile.prod
    container_name: dashads_backend_prod
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      SUPABASE_URL: ${SUPABASE_URL}
      SUPABASE_KEY: ${SUPABASE_KEY}
      JWT_SECRET: ${JWT_SECRET}
      ENVIRONMENT: production
    ports:
      - "127.0.0.1:8000:8000"  # Apenas localhost (Nginx fará proxy)
    volumes:
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

E `docker-compose.staging.yml` (mesma estrutura, porta diferente):

```yaml
version: '3.8'

services:
  backend-staging:
    build:
      context: .
      dockerfile: Dockerfile.prod
    container_name: dashads_backend_staging
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      SUPABASE_URL: ${SUPABASE_URL}
      SUPABASE_KEY: ${SUPABASE_KEY}
      JWT_SECRET: ${JWT_SECRET}
      ENVIRONMENT: staging
    ports:
      - "127.0.0.1:8001:8000"  # Porta diferente para staging
    volumes:
      - ./logs:/app/logs
```

### 5.4. Criar arquivo .env de produção
Na VPS, crie `/var/www/dashads/backend/.env.prod`:

```env
# Supabase Production
DATABASE_URL=postgresql://postgres:[SENHA]@db.[PROJECT_ID].supabase.co:5432/postgres
SUPABASE_URL=https://[PROJECT_ID].supabase.co
SUPABASE_KEY=[ANON_KEY]
SUPABASE_SERVICE_KEY=[SERVICE_ROLE_KEY]

# JWT
JWT_SECRET=[GERE_UMA_CHAVE_FORTE_AQUI]
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Environment
ENVIRONMENT=production
```

E `.env.staging` em `/var/www/dashads/backend-staging/` (com credenciais do projeto staging).

### 5.5. Gerar JWT_SECRET seguro
```bash
# Na VPS
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Use o resultado no JWT_SECRET
```

---

## Etapa 6: Deploy do backend

### 6.1. Enviar código para VPS
Opção A: Git (recomendado)
```bash
# Na VPS
cd /var/www/dashads/backend
git clone [SEU_REPOSITORIO] .

# Ou via SCP (do seu computador)
scp -r backend/* dashads@[IP_VPS]:/var/www/dashads/backend/
```

Opção B: SCP manual
```bash
# Do seu computador
scp -r c:\projetos\backend\dashads/* dashads@[IP_VPS]:/var/www/dashads/backend/
```

### 6.2. Configurar backend produção
```bash
# Na VPS
cd /var/www/dashads/backend

# Copiar arquivos de produção
cp Dockerfile.prod Dockerfile
cp docker-compose.prod.yml docker-compose.yml
cp .env.prod .env

# Build e iniciar
docker-compose build
docker-compose up -d

# Ver logs
docker-compose logs -f
```

### 6.3. Configurar backend staging
```bash
# Na VPS
cd /var/www/dashads/backend-staging
# Copie todos os arquivos do backend
cp -r /var/www/dashads/backend/* .

# Ajustar configurações
cp docker-compose.staging.yml docker-compose.yml
cp .env.staging .env

# Ajustar docker-compose.yml para usar .env.staging
# Build e iniciar
docker-compose build
docker-compose up -d
```

### 6.4. Verificar se está rodando
```bash
# Verificar containers
docker ps

# Testar localmente na VPS
curl http://localhost:8000/health  # Produção
curl http://localhost:8001/health  # Staging
```

---

## Etapa 7: Configurar Nginx

### 7.1. Configurar Nginx para produção
Crie `/etc/nginx/sites-available/dashads-prod`:

```nginx
# Backend API - Produção
server {
    listen 80;
    server_name api.marketdash.com.br;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}

# Frontend - Produção
server {
    listen 80;
    server_name app.marketdash.com.br;

    root /var/www/dashads/frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /static/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### 7.2. Configurar Nginx para staging
Crie `/etc/nginx/sites-available/dashads-staging`:

```nginx
# Backend API - Staging
server {
    listen 80;
    server_name api-staging.marketdash.com.br;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}

# Frontend - Staging
server {
    listen 80;
    server_name app-staging.marketdash.com.br;

    root /var/www/dashads/frontend-staging;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### 7.3. Ativar sites
```bash
# Criar links simbólicos
sudo ln -s /etc/nginx/sites-available/dashads-prod /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/dashads-staging /etc/nginx/sites-enabled/

# Remover default (opcional)
sudo rm /etc/nginx/sites-enabled/default

# Testar configuração
sudo nginx -t

# Recarregar Nginx
sudo systemctl reload nginx
```

---

## Etapa 8: Configurar SSL (HTTPS)

### 8.1. Obter certificados SSL
```bash
# Certificados para produção
sudo certbot --nginx -d api.marketdash.com.br -d app.marketdash.com.br

# Certificados para staging
sudo certbot --nginx -d api-staging.marketdash.com.br -d app-staging.marketdash.com.br
```

Responda as perguntas:
- Email: seu email
- Aceitar termos: Y
- Compartilhar email: N (ou Y)
- Redirect HTTP to HTTPS: 2 (Sim)

### 8.2. Verificar renovação automática
```bash
# Testar renovação
sudo certbot renew --dry-run
```

---

## Etapa 9: Deploy do frontend

### 9.1. Build do frontend (local ou na VPS)
Se você tem o frontend:

```bash
# Na sua máquina local ou na VPS
cd [caminho_do_frontend]
npm install
npm run build
# Isso cria a pasta 'dist' ou 'build'
```

### 9.2. Enviar build para VPS
```bash
# Do seu computador
scp -r [caminho_do_frontend]/dist/* dashads@[IP_VPS]:/var/www/dashads/frontend/
scp -r [caminho_do_frontend]/dist/* dashads@[IP_VPS]:/var/www/dashads/frontend-staging/
```

### 9.3. Configurar permissões
```bash
# Na VPS
sudo chown -R www-data:www-data /var/www/dashads/frontend
sudo chown -R www-data:www-data /var/www/dashads/frontend-staging
```

### 9.4. Configurar variáveis do frontend
No frontend, configure as URLs:

```env
# .env.production
VITE_SUPABASE_URL=https://[PROJECT_ID].supabase.co
VITE_SUPABASE_ANON_KEY=[ANON_KEY]
VITE_API_URL=https://api.marketdash.com.br

# .env.hml (homologação)
VITE_SUPABASE_URL=https://[HML_PROJECT_ID].supabase.co
VITE_SUPABASE_ANON_KEY=[HML_ANON_KEY]
VITE_API_URL=https://api.marketdash.hml.com.br
```

---

## Etapa 10: Integração Supabase + Backend

### 10.1. Atualizar backend para usar Supabase
Você precisará adaptar o código para usar Supabase Auth ao invés de JWT customizado. Isso é uma mudança maior que pode ser feita depois.

Por enquanto, o backend pode continuar usando sua autenticação atual enquanto conecta ao PostgreSQL do Supabase.

### 10.2. Testar conexão
```bash
# Na VPS, testar conexão com Supabase
docker exec -it dashads_backend_prod python3 -c "
from app.db.session import engine
with engine.connect() as conn:
    print('Conexão OK!')
"
```

---

## Etapa 11: Verificação final

### 11.1. Checklist
```bash
# Backend produção
curl https://api.marketdash.com.br/health

# Backend homologação
curl https://api.marketdash.hml.com.br/health

# Frontend produção
curl https://marketdash.com.br

# Frontend homologação
curl https://marketdash.hml.com.br

# Ver containers rodando
docker ps

# Ver logs
docker logs dashads_backend_prod
docker logs dashads_backend_staging
```

### 11.2. Monitoramento básico
```bash
# Ver uso de recursos
htop

# Ver logs do Nginx
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# Ver espaço em disco
df -h
```

---

## Etapa 12: Automação (opcional)

### 12.1. Script de deploy
Crie `/var/www/dashads/deploy.sh`:

```bash
#!/bin/bash
ENV=$1  # prod ou staging

if [ "$ENV" == "prod" ]; then
    cd /var/www/dashads/backend
    git pull
    docker-compose build
    docker-compose up -d
elif [ "$ENV" == "staging" ]; then
    cd /var/www/dashads/backend-staging
    git pull
    docker-compose build
    docker-compose up -d
fi
```

```bash
chmod +x /var/www/dashads/deploy.sh
```

Uso:
```bash
./deploy.sh prod
./deploy.sh staging
```

---

## Resumo dos endereços finais

### Produção:
- Frontend: `https://marketdash.com.br`
- API: `https://api.marketdash.com.br`
- Docs API: `https://api.marketdash.com.br/docs`

### Homologação:
- Frontend: `https://marketdash.hml.com.br`
- API: `https://api.marketdash.hml.com.br`
- Docs API: `https://api.marketdash.hml.com.br/docs`

---

## Próximos passos recomendados

1. Configurar backups automáticos
2. Configurar monitoramento (Uptime Robot, etc.)
3. Configurar firewall (UFW)
4. Configurar log rotation
5. Implementar CI/CD (GitHub Actions)
6. Migrar autenticação para Supabase Auth

Quer que eu detalhe alguma etapa específica ou ajude com a integração Supabase no backend?
