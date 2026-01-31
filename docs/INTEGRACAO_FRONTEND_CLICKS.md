# Guia de Integração Frontend - API de Cliques (Clicks)

Este documento descreve como o frontend deve consumir a nova API de Cliques para exibir os dados de canais, sub IDs e horários.

## 1. Endpoints Disponíveis

Todos os endpoints requerem autenticação via Bearer Token e assinatura ativa.

### Upload de CSV de Cliques
- **URL**: `/api/v1/clicks/upload`
- **Método**: `POST`
- **Corpo**: `multipart/form-data` com campo `file`.
- **Uso**: Quando o usuário seleciona um arquivo de relatório de cliques para subir.

### Listar Cliques do Último Upload
- **URL**: `/api/v1/clicks/latest/rows`
- **Método**: `GET`
- **Parâmetros Query**:
  - `start_date` (opcional): Data inicial (YYYY-MM-DD)
  - `end_date` (opcional): Data final (YYYY-MM-DD)
  - `limit` (opcional): Número de registros
- **Uso**: Ideal para o Dashboard Principal para mostrar os dados mais recentes.

### Listar Histórico de Cliques
- **URL**: `/api/v1/clicks/all/rows`
- **Método**: `GET`
- **Uso**: Para telas de relatórios históricos e comparativos.

### Deletar Todos os Dados
- **URL**: `/api/v1/clicks/all`
- **Método**: `DELETE`
- **Uso**: Para limpar todos os dados de cliques do usuário.

## 2. Formato de Resposta (JSON)

Cada linha de clique retornada segue este formato:

```json
{
  "id": 123,
  "date": "2026-01-30",
  "time": "20:52:00",
  "channel": "Instagram",
  "clicks": 5244,
  "sub_id": "dispenser01----",
  "dataset_id": 45,
  "user_id": 10,
  "raw_data": { ... }
}
```

## 3. Como Implementar os Gráficos das Imagens

### Total de Cliques
Soma do campo `clicks` de todos os objetos retornados.

### Cliques por Canal (Tabela/Pizza)
Agrupar os dados pelo campo `channel` e somar os `clicks`.
Exemplo de cálculo de porcentagem: `(clicks_do_canal / total_cliques) * 100`.

### Cliques por Hora
Utilizar o campo `time` para agrupar em faixas horárias (ex: 22:00 - 22:59).

### Cliques por Sub ID
Agrupar pelo campo `sub_id` e somar os `clicks`.

## 4. Exemplo de Chamada (Axios)

```javascript
const getLatestClicks = async (startDate, endDate) => {
  try {
    const response = await api.get('/api/v1/clicks/latest/rows', {
      params: {
        start_date: startDate,
        end_date: endDate
      }
    });
    return response.data;
  } catch (error) {
    console.error("Erro ao buscar cliques:", error);
  }
};
```
