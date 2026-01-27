# 🚀 Início Rápido - MarketDash Backend

## ⚠️ PROBLEMA ATUAL: Docker Desktop não está rodando

O erro que você está vendo significa que o **Docker Desktop precisa ser iniciado manualmente**.

---

## ✅ SOLUÇÃO RÁPIDA (3 passos)

### 1️⃣ Iniciar Docker Desktop

**Opção A - Script automático:**
```bash
# No PowerShell ou CMD
.\start-docker.bat
```

**Opção B - Manual:**
1. Pressione `Win + R`
2. Digite: `"C:\Program Files\Docker\Docker\Docker Desktop.exe"`
3. Pressione Enter
4. **AGUARDE** até o ícone do Docker ficar **VERDE** na bandeja do sistema (canto inferior direito)

### 2️⃣ Verificar se está rodando

```bash
docker info
```

Se funcionar, você verá informações do servidor (não apenas do cliente).

### 3️⃣ Executar o projeto

```bash
docker compose up
```

---

## 🔍 Como saber se o Docker está pronto?

✅ **Pronto quando:**
- O ícone do Docker na bandeja do sistema está **verde**
- O comando `docker info` mostra informações do **Server** (não apenas Client)
- Não aparece erro de "pipe" ou "cannot find file"

❌ **Ainda não está pronto quando:**
- O ícone está cinza ou não aparece
- `docker info` mostra erro de conexão
- Aparece erro "cannot find file specified"

---

## 🐛 Se o Docker Desktop não iniciar

1. **Reinicie o computador**
2. **Verifique se o WSL 2 está instalado:**
   ```powershell
   wsl --status
   ```
3. **Reinstale o Docker Desktop** se necessário

---

## 💡 Alternativa: Executar sem Docker

Se preferir testar sem Docker agora:

### Pré-requisitos:
- Python 3.11+
- PostgreSQL instalado

### Passos:

1. **Instalar dependências:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configurar PostgreSQL:**
   - Crie banco: `marketdash_db`
   - Crie usuário: `marketdash_user` / senha: `marketdash_password`

3. **Criar arquivo `.env`:**
   ```env
   DATABASE_URL=postgresql://marketdash_user:marketdash_password@localhost:5432/marketdash_db
   JWT_SECRET=your-secret-key-change-in-production-min-32-chars
   ```

4. **Executar:**
   ```bash
   uvicorn app.main:app --reload
   ```

---

## 📞 Ainda com problemas?

Veja o arquivo `TROUBLESHOOTING.md` para mais soluções.

