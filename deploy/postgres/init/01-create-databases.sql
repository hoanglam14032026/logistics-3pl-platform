-- Chạy một lần khi khởi tạo volume PostgreSQL lần đầu (docker-entrypoint-initdb.d).
-- CSDL dev (logistics_3pl_dev) do POSTGRES_DB tạo; ở đây tạo thêm CSDL test.
CREATE DATABASE logistics_3pl_test OWNER logistics;
