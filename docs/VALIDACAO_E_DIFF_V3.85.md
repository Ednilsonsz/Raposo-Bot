# Validação e diff — 3.84 COR R6 → 3.85 R6

## Base

Base oficial utilizada: `CONSOLIDADO_bullex_update_v3.84_COR_R6.zip`.

A tentativa anterior da 3.85 não foi usada como base.

## Preservado

- Dashboard e Últimas Operações.
- DIDI/DIDI NEW e Duplo Pullback/NEW.
- score e filtro de ROI negativo por setup.
- resultados e contadores oriundos da Bullex.
- identificação do ativo, expiração M1, executor e janela de envio em `:57`.

## Implementado

- Ranking adaptativo 1–5 por setup com amostra mínima, janela recente, suavização e confiança.
- Bootstrap do histórico existente somente com WIN/LOSS reais confirmados pela Bullex.
- Fallback 2/3 para setups NEW apenas enquanto não existe amostra confiável.
- CANDLE_FLOW ativo: EMA9/EMA21, RSI, força, pavios, price action, últimos candles, volatilidade e payout.
- REVZ ativo: média/desvio de 120 velas, Z-score e confirmação de retorno antes de sinalizar.
- SR_NIVEL ativo: pivôs, nível objetivo, tolerância por ATR, toque, número de ocorrências e rejeição.
- Confirmações e vetos sobre DIDI/DP registrados em log e na tabela `strategy_module_events`.
- Dashboard com W/L/WR real por setup e ranking adaptativo por posição.

## Arquivos ativos novos/alterados

- `main_v385_r6.py`
- `realtime_analyzer_v385_r6.py`
- `score_engine_v385_r6.py`
- `demo_executor_v385_r6.py`
- `ui_overlay_massa_v385.py`
- `adaptive_position_v385.py`
- `strategy_modules_v385.py`
- `config_v385_release.json`
- `config.json`
- `r6_restart.py`
- `VERSION`
- `INICIAR_RAPOSO_V3_85_R6.bat`
- `tests/test_v385_features.py`
- `tests/offline_validate_v385.py`

Os launchers 3.84 foram retirados apenas do pacote 3.85 para evitar inicialização acidental da versão antiga. A base oficial original permaneceu intacta na área de trabalho.

## Validações

- Compilação Python dos arquivos ativos: OK.
- JSON das duas configurações 3.85: OK.
- DEMO ativa, execução ativa, expiração M1 e `entry_send_second=57`: OK.
- 6 testes específicos da 3.85: 6 OK, incluindo gravação real no schema `demo_orders` de 14 colunas.
- Validação offline no banco histórico: 18 ativos, 19.080 janelas.
- Sinais encontrados sem exceção: CANDLE_FLOW 6.523; REVZ 709; SR_NIVEL 2.259; DIDI NEW 623; DP NEW 1.092.

## Suíte antiga da 3.84

A suíte legada foi executada separadamente. Os testes de restart compatíveis passaram, mas 45 testes antigos falharam por incompatibilidade já existente entre o fixture de teste (`demo_orders` com 11 valores) e o schema atual da base (14 colunas), além de um teste que extrai um fragmento antigo do controle de restart. Essas falhas não foram mascaradas nem tratadas como regressões da 3.85; os testes novos e a validação offline passaram.

## Correção COR_R1 após avaliação dos logs

- Corrigido `INSERT OR REPLACE` do executor: 14 colunas agora recebem exatamente 14 valores.
- Corrigida a consulta do Dashboard para não depender de `o.executed_at` durante restauração/troca de schema.
- O teste reproduz o schema observado no banco real e confirma criação de `demo_orders` e atualização da decisão para `EXECUTED`.
