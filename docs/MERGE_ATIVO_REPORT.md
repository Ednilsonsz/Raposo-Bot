# Consolidação R6 — ativo dinâmico

## Base escolhida

A base principal é `bullex_update_v3.84_COR_R6` (primeira pasta). Ela foi preservada como fonte integral do consolidado.

## Resultado da comparação

A primeira base já contém a correção mais segura para identificação e troca dinâmica de ativo:

- `bullex_live_capture.py` começa sem ativo operacional confirmado (`asset_id` vazio) e exige confirmação do gráfico com o WebSocket M1.
- `main_v384_r6.py` publica o par confirmado pelo snapshot gráfico/WebSocket e bloqueia ordens quando o ativo não está verificado.
- `database.py` mantém a migração aditiva do `asset_catalog`.
- `broker_results_r6.py` inicializa o catálogo e preserva o vínculo operacional entre ordem, ativo da Bullex e resultado confirmado.
- `demo_executor_v384_r6.py` grava os campos catalogados e valida o ativo antes da execução.

## Diferenças rejeitadas da segunda base

A segunda base (`R6_RECUPERADO_1711_CORRIGIDO`) não foi aplicada nesses módulos porque, no conteúdo recebido, ela remove ou enfraquece partes da master:

- inicializa `bullex_live_capture` com o `asset_id` configurado;
- deriva imediatamente o nome a partir desse ID;
- usa o ID configurado como fallback operacional;
- remove `asset_catalog.py` e suas migrações de `database.py`;
- remove a inicialização do catálogo em `broker_results_r6.py`;
- remove os campos catalogados e `_current_asset_name` do executor.

Essas diferenças poderiam reintroduzir justamente o risco de carregar o ativo anterior durante uma troca.

## Escopo preservado

Nenhuma alteração foi feita em setups, DIDI, DIDI NEW, Duplo Pullback, score, ciclos, expiração, motor, dashboard ou regras de resultado. O consolidado é uma cópia da primeira base, com a confirmação de que a correção de ativo da master é superior à versão recuperada recebida.

## Validação

A validação final deve executar `python3 -m compileall -q .` e a suíte existente em `tests/`. A pasta `__pycache__` não é fonte de execução e deve ser desconsiderada ao copiar para o Windows.

## Correções finais adicionadas após a revisão

O contador de fechamento foi ajustado para não apagar o último valor válido quando uma atualização temporária chega nula. O JavaScript agora mantém o deadline visual existente e não aplica mais expiração artificial de três segundos que fazia o painel mostrar `—` antes de voltar ao tempo. O Python continua usando o relógio visual da Bullex como fonte principal, com fallback numérico local.

Também foi corrigido o caso de WebSocket fornecer um `active_id` que não existe no catálogo. Esse caso agora retorna `verified=False`, limpa o ID operacional e exibe `ATIVO NÃO CADASTRADO`. O `DemoExecutor` possui uma segunda trava explícita e bloqueia a ordem com o motivo `ativo não cadastrado`.

## Dashboard — últimas operações e posição Bullex

A tabela `ÚLTIMAS OPERAÇÕES` pertence exclusivamente ao bloco Dashboard. A primeira coluna agora é `Posição` e usa, nesta ordem, `broker_confirmed_results.broker_id`, `broker_order_links.broker_id` ou um fallback local. Assim, quando a Bullex confirma uma posição como `BL 14278051203`, esse mesmo identificador aparece no painel junto ao resultado WIN/LOSS e ao lucro/prejuízo. A tela Clássica não foi alterada.

## Modo LOG e catálogo próprio de ativos

O botão `COMPACTO` foi substituído por `LOG`. O modo LOG reutiliza o mesmo `bxlogpanel` e `bxlogbody` da tela Clássica, mantendo o log do runtime sem alterar a tela Dashboard.

O catálogo próprio agora também expõe a visão `ativos` com as colunas de negócio `My_ID`, `ID_BULLEX` e `NOME`. O nome lido no cabeçalho do gráfico é registrado imediatamente com o ID provisório disponível, como `76` ou `86`, mas permanece `verified=False` e não libera ordens. Quando o resultado da Bullex chega, o `active_id` real, como `14278051203`, é associado ao mesmo `My_ID` e ao mesmo `NOME`. Assim, a tabela mantém o vínculo histórico entre IDs provisórios e IDs finais sem substituir o ativo anterior.

## Ajustes finais solicitados

O contador `Fecha` permanece estritamente visual e não interfere no gatilho operacional de envio em `00:57`. O fallback visual passou a usar diretamente o deadline do feed, sem atraso artificial de três segundos.

O botão rotulado `LOG` agora usa a estrutura compacta funcional, deixa o painel de log visível ao entrar nesse modo e abre o conteúdo de `bxlogbody`; o botão `VER LOG` da tela Clássica continua carregando o mesmo log.

A captura de ativos passou a reconhecer nomes no formato `EUR-USD(OTC)` e variantes equivalentes. Ao detectar um novo nome no gráfico, cria imediatamente o `My_ID` no catálogo próprio, com o ID provisório disponível (`76/86`) ou sem ID quando ainda não houver nenhum. O ativo continua não verificado e bloqueado para execução. O `active_id` final da Bullex é vinculado posteriormente ao mesmo `My_ID`.

## Expiração durante troca de ativo

A rotina que tentava ler, clicar ou selecionar novamente `1 MIN` foi removida do caminho operacional. Após uma troca de ativo, a expiração entregue pela Bullex é preservada; a entrada continua usando exclusivamente o relógio do feed e a janela do candle (`:57/:58`). Nenhum atraso de recálculo ou clique de controle é introduzido.

## Correção de reconhecimento do ativo no gráfico

O detector deixou de exigir que todos os textos de pares visíveis fossem iguais. Ele agora prioriza o texto de par mais específico e curto do cabeçalho do gráfico, evitando que menus/ancestrais com outros ativos causem `ATIVO NÃO IDENTIFICADO`. O formato `NZD/USD(OTC)` é normalizado para `NZDUSD-OTC` e associado ao catálogo/ID conhecido quando o feed confirma o ativo.

## Execução pelo clique do gráfico

O bloqueio por ausência de `active_id` foi removido para o caminho operacional. A Bullex recebe a ordem pelo clique no botão de execução do gráfico selecionado; o nome capturado e o `My_ID` local são suficientes para manter o ativo operacional. O `active_id` retornado depois continua sendo vinculado ao mesmo cadastro, mas não impede o clique inicial.

## Correção do rollover do Fecha

O cálculo visual foi ajustado de `60 - segundo` para `59 - segundo`. Assim, o valor máximo exibido é `00:59`; no segundo `59` chega a zero e, na virada, inicia o próximo candle sem produzir `01:00`. A regra operacional do executor permanece inalterada.

## Mapeamento NZD/USD(OTC)

O runtime observou `active_id=80` ao selecionar `NZD/USD(OTC)`. O catálogo em memória agora normaliza `80` para `NZDUSD-OTC`, permitindo que a seleção seja reconhecida imediatamente mesmo quando o cabeçalho DOM não aparece.

## Mapeamento EUR/GBP(OTC)

O runtime observou `active_id=77` ao selecionar EUR/GBP(OTC). O catálogo em memória agora normaliza `77` para `EURGBP-OTC`, separado de EUR/USD (`76`) e NZD/USD (`80`).

## Mapeamento EUR/JPY(OTC)

O runtime observou `active_id=79` ao selecionar EUR/JPY(OTC). O catálogo em memória agora normaliza `79` para `EURJPY-OTC`, separado de EUR/GBP (`77`).

## Regra operacional 00:57

O campo `Fecha` é somente visual. Para setup favorável, a configuração operacional `auto_demo.entry_send_second` foi fixada em `57` nos arquivos de configuração, e o executor usa o relógio da Bullex para clicar nessa janela. O contador visual não participa da decisão da ordem.

## Falha pós-clique da ordem

Na tentativa analisada, o log confirmou `clique CDP enviado` no botão ABAIXO, mas o registro posterior no SQLite lançou `OperationalError`. O executor agora trata a gravação pós-clique separadamente: se o clique foi enviado, registra `SUBMETIDA` em memória/log e não transforma a operação em falha nem repete o clique no mesmo ciclo. O log futuro também exibirá a mensagem completa da exceção SQLite.

## Log de setups favoráveis em Últimas Operações

Todo setup que atingir a condição favorável agora cria um registro antes da tentativa de execução, com `setup`, score, direção e motivo `SETUP FAVORÁVEL`. A mesma linha é atualizada depois para `SUBMITTED`, `BLOCKED`, `ERROR` ou `SKIPPED`, mantendo a explicação no campo `Info`. Assim, setups favoráveis executados e não executados permanecem visíveis no Dashboard.

## Reconciliação de histórico sem vínculo

O histórico da Bullex agora tenta recuperar posições sem `broker_order_links` somente quando existe uma decisão local compatível por direção, valor, ativo e janela temporal do candle. A posição é então vinculada ao `broker_id`, marcada como executada e liquidada pelo resultado histórico. Posições manuais sem correspondência local continuam fora dos contadores.

## Migração de cycle_start e restauração do overlay

A base local antiga não possuía `demo_orders.cycle_start`, embora executor, reconciliador e Dashboard já dependessem dessa coluna. Isso causava `OperationalError` após o clique e impedia a atualização do overlay. Foi adicionada migração aditiva automática em `asset_catalog.ensure_schema()`, preservando o histórico existente.
