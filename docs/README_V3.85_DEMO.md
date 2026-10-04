# Raposo 3.85 R6 — teste em conta DEMO

Inicie somente por `INICIAR_RAPOSO_V3_85_R6.bat`.

## O que foi preservado da 3.84 COR R6

- Dashboard completo, incluindo **Últimas Operações**.
- DIDI, DIDI NEW, Duplo Pullback e Duplo Pullback NEW.
- score, bloqueio de setup com ROI negativo, resultados reais da Bullex e contadores.
- identificação do ativo, expiração M1, executor e envio em `:57`.

## Novidades ativas somente em DEMO

- Ranking adaptativo das posições 1–5 separado por setup.
- Fallback seguro 2/3 para setups NEW enquanto nenhuma posição atingir a amostra mínima.
- Depois da amostra mínima, o histórico real WIN/LOSS da Bullex substitui a regra fixa.
- `CANDLE_FLOW`, `REVZ` e `SR_NIVEL` como setups identificáveis e mensuráveis.
- Registro de confirmação/veto dos módulos sobre DIDI/DP em `strategy_module_events` e nos logs.
- Dashboard com W/L/WR real por setup e tabela do ranking adaptativo.

Os módulos não alteram código nem parâmetros automaticamente. Eles apenas tomam decisões dentro das regras de `config_v385_release.json`.

## Parâmetros principais

- `adaptive_position.min_samples_per_position`: 12
- `adaptive_position.fallback_new_positions`: `[2, 3]`
- `auto_demo.entry_send_second`: 57
- `auto_demo.require_expiry_1m`: `true`
- `demo_only`: `true`
- `execute_orders`: `true`

