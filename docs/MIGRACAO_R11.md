# Migração da R11 para Raposo / Raposo_Data

## Caminhos ativos

- Aplicação: `L:\Bullex_Robo\Raposo\app`
- Configuração e credenciais: `L:\Bullex_Robo\Raposo\config`
- Testes: `L:\Bullex_Robo\Raposo\tests`
- Scripts: `L:\Bullex_Robo\Raposo\scripts`
- Documentação: `L:\Bullex_Robo\Raposo\docs`
- Banco principal e snapshot: `L:\Bullex_Robo\Raposo_Data\database`
- Logs: `L:\Bullex_Robo\Raposo_Data\logs`
- Relatórios: `L:\Bullex_Robo\Raposo_Data\reports`
- Histórico: `L:\Bullex_Robo\Raposo_Data\history`
- Estado de execução: `L:\Bullex_Robo\Raposo_Data\runtime`
- Backups: `L:\Bullex_Robo\Raposo_Data\backups`

O módulo `app\paths_r11.py` centraliza os caminhos. A variável `RAPOSO_DATA_ROOT` pode apontar para outra raiz de dados; sem ela, usa `L:\Bullex_Robo\Raposo_Data`.

## Inicialização

Use `L:\Bullex_Robo\Raposo\scripts\INICIAR_RAPOSO_R11.bat`. O BAT define a raiz de dados e inicia o código em `app`.

## Cópia e acesso anterior

A instalação original em `L:\Bullex_Robo\APP_NEW\Raposo_3.86_R11` foi mantida intacta. Os bancos foram copiados, não movidos. Não apague a origem até confirmar o acesso e a operação pela nova inicialização.

## Organização dos dados

- `database\candles.db`: banco principal copiado da R11.
- `database\candles_latest.db`: snapshot canônico copiado de `L:\Bullex_Robo\Dados`.
- `runtime\config_execucao.json`: configuração operacional persistente.
- `runtime\resume_state_r11.json`: estado de retomada copiado da R11.
- `logs\`: logs locais e compartilhados copiados das origens.
- `reports\`: capturas de resultado de 18 a 26/09/2026.

O primeiro uso da sincronização continuará publicando `candles_latest.db` dentro de `Raposo_Data\database`.

## Versão anterior

Manter como backup de versão anterior somente a 3.85-R6. Os arquivos de origem de outras versões continuam disponíveis na instalação antiga/backup existente; não foram removidos nesta migração.
