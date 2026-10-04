"""Ranking adaptativo 1-5 por setup, alimentado por resultados reais da Bullex."""

import math
from datetime import datetime


class AdaptivePositionRanker:
    def __init__(self, db, config):
        self.db = db
        self.config = config or {}
        self._ensure_schema()
        self._bootstrap_existing_results()

    def _ensure_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS adaptive_position_decisions(
              decision_id INTEGER PRIMARY KEY,
              asset_id TEXT NOT NULL,
              setup TEXT NOT NULL,
              signal_ts INTEGER NOT NULL,
              position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 5),
              mode TEXT NOT NULL,
              reason TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_adaptive_setup_position
              ON adaptive_position_decisions(setup,position,created_at);
        """)
        self.db.commit()

    def _bootstrap_existing_results(self):
        """Classifica o histórico já confirmado sem usar o outcome sintético de candles."""
        rows = self.db.execute("""
            SELECT DISTINCT d.id,d.asset_id,d.c1_dir,d.cycle_start
              FROM decisions d
              JOIN demo_orders o ON o.decision_id=d.id AND o.status='EXECUTED'
              JOIN broker_confirmed_results r ON r.demo_order_id=o.id
             WHERE d.c1_dir IS NOT NULL
               AND r.result IN ('WIN','LOSS','WIN_GALE','LOSS_GALE')
               AND NOT EXISTS(
                 SELECT 1 FROM adaptive_position_decisions a WHERE a.decision_id=d.id
               )
             ORDER BY d.id
        """).fetchall()
        inserted = 0
        for decision_id, asset_id, setup, cycle_start in rows:
            signal = self.db.execute("""
                SELECT signal_ts
                  FROM score_signals
                 WHERE asset_id=? AND setup=? AND signal_ts IN (?,?)
                 ORDER BY ABS(signal_ts-?) ASC,id DESC LIMIT 1
            """, (str(asset_id), str(setup), int(cycle_start), int(cycle_start) - 60,
                  int(cycle_start))).fetchone()
            if not signal:
                continue
            signal_ts = int(signal[0])
            position = self.position_for(asset_id, setup, signal_ts)
            self.db.execute("""
                INSERT OR IGNORE INTO adaptive_position_decisions(
                  decision_id,asset_id,setup,signal_ts,position,mode,reason,created_at
                ) VALUES(?,?,?,?,?,'HISTORICAL','bootstrap resultado real Bullex',?)
            """, (int(decision_id), str(asset_id), str(setup), signal_ts, position,
                  datetime.now().isoformat(timespec="seconds")))
            inserted += 1
        if inserted:
            self.db.commit()

    def position_for(self, asset_id, setup, signal_ts):
        row = self.db.execute("""
            SELECT COUNT(DISTINCT signal_ts)
              FROM score_signals
             WHERE asset_id=? AND setup=? AND signal_ts<=?
        """, (str(asset_id), str(setup), int(signal_ts))).fetchone()
        ordinal = int((row or [0])[0] or 0)
        return ((ordinal - 1) % 5) + 1 if ordinal else 1

    @staticmethod
    def _wilson_lower(wins, total, z=1.2815515655446004):
        if total <= 0:
            return 0.0
        p = wins / total
        denominator = 1.0 + z * z / total
        centre = p + z * z / (2.0 * total)
        margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
        return (centre - margin) / denominator

    def _real_rows(self, setup):
        """Usa somente WIN/LOSS confirmados pela corretora; nunca outcome de candle."""
        return self.db.execute("""
            SELECT a.position,r.result,r.closed_at
              FROM adaptive_position_decisions a
              JOIN demo_orders o ON o.decision_id=a.decision_id AND o.status='EXECUTED'
              JOIN broker_confirmed_results r ON r.demo_order_id=o.id
             WHERE a.setup=? AND r.result IN ('WIN','LOSS','WIN_GALE','LOSS_GALE')
             ORDER BY r.closed_at DESC,r.demo_order_id DESC
        """, (str(setup),)).fetchall()

    def rankings(self, setup):
        rows = self._real_rows(setup)
        recent_window = int(self.config.get("recent_window", 30))
        prior_wins = float(self.config.get("prior_wins", 2.0))
        prior_losses = float(self.config.get("prior_losses", 2.0))
        output = []
        for position in range(1, 6):
            outcomes = [result for pos, result, _closed in rows if int(pos) == position]
            wins = sum(1 for result in outcomes if result in ("WIN", "WIN_GALE"))
            losses = sum(1 for result in outcomes if result in ("LOSS", "LOSS_GALE"))
            recent = outcomes[:recent_window]
            recent_wins = sum(1 for result in recent if result in ("WIN", "WIN_GALE"))
            recent_total = len(recent)
            total = wins + losses
            historical_wr = wins / total if total else 0.5
            recent_wr = recent_wins / recent_total if recent_total else historical_wr
            smoothed_wr = (wins + prior_wins) / (total + prior_wins + prior_losses)
            blended_wr = 0.65 * smoothed_wr + 0.35 * recent_wr
            lower = self._wilson_lower(wins, total)
            output.append({"position": position, "samples": total, "wins": wins, "losses": losses,
                           "wr": historical_wr, "recent_wr": recent_wr, "score": blended_wr,
                           "confidence_lower": lower})
        return sorted(output, key=lambda item: (item["score"], item["samples"]), reverse=True)

    def evaluate(self, setup, position):
        if not self.config.get("enabled", True):
            return True, "DISABLED", "ranking adaptativo desligado", self.rankings(setup)
        ranking = self.rankings(setup)
        # User authorized all ordinal positions for the seven active setups on 01/10.
        if setup in ("DIDI", "DIDI NEW", "DUPLO_PULLBACK", "DUPLO_PULLBACK NEW", "CANDLE_FLOW", "REVZ", "SR_NIVEL", "LA_MAGIA", "Estrategy_TG"):
            allowed = int(position) in (1, 2, 3, 4, 5)
            return allowed, "ALL_POSITIONS", f"{setup}: pos={position}; liberacao manual; ativas=[1, 2, 3, 4, 5]", ranking
        minimum = int(self.config.get("min_samples_per_position", 12))
        confident = [item for item in ranking if item["samples"] >= minimum]
        fallback = [int(value) for value in self.config.get("fallback_new_positions", [2, 3])]
        if not confident:
            allowed = position in fallback if str(setup).endswith(" NEW") else True
            reason = f"fallback seguro; nenhuma posição com amostra>={minimum}; permitidas={fallback if str(setup).endswith(' NEW') else '1-5'}"
            return allowed, "FALLBACK", reason, ranking

        min_wr = float(self.config.get("min_win_rate", 0.52))
        min_lower = float(self.config.get("min_confidence_lower", 0.40))
        max_positions = max(1, min(5, int(self.config.get("max_active_positions", 3))))
        eligible = [item for item in confident if item["score"] >= min_wr and item["confidence_lower"] >= min_lower]
        if not eligible:
            eligible = confident[:1]
        enabled_positions = {item["position"] for item in eligible[:max_positions]}
        current = next(item for item in ranking if item["position"] == int(position))
        allowed = int(position) in enabled_positions
        reason = (f"pos={position}; n={current['samples']}; WR={current['wr']*100:.1f}%; "
                  f"WRrec={current['recent_wr']*100:.1f}%; conf={current['confidence_lower']*100:.1f}%; "
                  f"ativas={sorted(enabled_positions)}")
        return allowed, "ADAPTIVE", reason, ranking

    def record(self, decision_id, asset_id, setup, signal_ts, position, mode, reason):
        self.db.execute("""
            INSERT OR REPLACE INTO adaptive_position_decisions(
              decision_id,asset_id,setup,signal_ts,position,mode,reason,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
        """, (int(decision_id), str(asset_id), str(setup), int(signal_ts), int(position),
              str(mode), str(reason), datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
