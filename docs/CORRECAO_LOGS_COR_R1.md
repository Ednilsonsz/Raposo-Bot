# Correção COR_R1

Os logs da execução em DEMO mostraram oito decisões autorizadas, sete cliques submetidos e nenhuma ordem persistida. A causa era um `INSERT` com 15 valores para 14 colunas em `demo_orders`.

Também havia repetição de `no such column: o.executed_at` no snapshot do Dashboard durante restaurações do banco.

Este patch:

- corrige a quantidade de placeholders do executor;
- mantém o registro da ordem e o estado `EXECUTED` após clique confirmado;
- elimina a dependência do Dashboard em `executed_at`, usando `requested_at` e `cycle_start`;
- preserva ranking adaptativo, módulos, ROI, M1 e envio em `:57`.

Validação específica: 6 testes executados, todos aprovados.
