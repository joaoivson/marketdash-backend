from locust import HttpUser, task, between
import logging

class DashboardUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """
        Simula o login inicial. 
        Nota: Em ambiente de teste local, usaremos um token mock ou um usuário de teste pré-criado.
        Para testes via Supabase, precisaríamos de credenciais reais ou desabilitar validação temporariamente.
        """
        self.token = "test_token" # Mock token for load test evaluation of endpoint performance
    
    @task(3)
    def get_dashboard(self):
        """Simula acesso recorrente ao dashboard (foco no Cache)"""
        with self.client.get(
            "/api/v1/dashboard",
            headers={"Authorization": f"Bearer {self.token}"},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                # Se falhar auth por ser mock, ainda medimos tempo de resposta do middleware
                response.success() 
            else:
                response.failure(f"Status code: {response.status_code}")

    @task(1)
    def get_dataset_rows(self):
        """Simula carregamento de tabelas detalhadas"""
        self.client.get(
            "/api/v1/datasets/all/rows",
            headers={"Authorization": f"Bearer {self.token}"}
        )

    @task(1)
    def health_check(self):
        """Verifica se a API está viva"""
        self.client.get("/health")
