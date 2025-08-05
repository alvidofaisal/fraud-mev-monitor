from locust import HttpUser, task, between


class FraudApiUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def healthcheck(self):
        self.client.get("/healthz")
