# Correção: catálogo próprio de ativos no dashboard

Esta cópia adiciona um catálogo persistente que separa o identificador interno da Bullex do identificador usado pelo dashboard.

## Modelo

`asset_catalog` guarda o nosso ID estável e o nome exibido. `asset_bullex_ids` guarda os `active_id` fornecidos pela Bullex e permite aliases confirmados para o mesmo ativo. Cada tentativa registrada em `demo_orders` recebe `our_asset_id`, `asset_name` e `bullex_active_id`, inclusive quando o status é `BLOCKED`, `REJECTED`, `SKIPPED` ou `ERROR`.

A coluna visual do dashboard usa `demo_orders.asset_name`; nunca usa o `active_id` bruto. Histórico anterior sem nome fica como `ATIVO NÃO VINCULADO` e não é renomeado por inferência. Cada tentativa também grava no log uma linha `[ATIVO][CATALOGO]` com `decision_id`, `our_asset_id`, `asset_name`, `bullex_active_id` e `status`.

## Arquivos alterados

- `asset_catalog.py`: catálogo e resolução persistente.
- `database.py`: migração aditiva ao abrir o banco.
- `demo_executor_v384_r6.py`: snapshot do ativo antes de cada tentativa.
- `broker_results_r6.py`: garante a migração ao inicializar o broker.
- `main_v384_r6.py`: registra no catálogo a combinação confirmada por gráfico + WebSocket.
- `bullex_live_capture.py`: não expõe `ATIVO ID ...` na interface.
- `ui_overlay_massa_v4.py`: apresenta o nome salvo na operação.

## Instalação

Faça backup da pasta em execução. Copie os arquivos acima para a pasta real do projeto, mantendo os nomes originais, e reinicie o robô. A migração é aditiva e não apaga as tabelas existentes. A primeira nova tentativa após a atualização cria o catálogo e grava o nome do ativo no histórico.

## Validação

A sintaxe Python foi compilada com sucesso. Também foram testadas a criação do catálogo, a associação `nome + active_id`, a migração de `demo_orders` e a gravação de uma tentativa `BLOCKED` com o snapshot do ativo.
