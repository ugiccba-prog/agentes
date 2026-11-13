"""Solicitudes paper del titular del repositorio, confirmadas desde GitHub."""
import os
from datetime import datetime, timezone
import requests

PRESETS = {
    "🦈 Violenta & Sagaz (Recomendado)": {
        "base_risk_pct": 1.5,
        "max_risk_pct": 3.0,
        "max_leverage": 5.0,
        "max_total_open_risk_pct": 10.0,
        "daily_loss_limit_pct": 5.0,
        "min_rr": 2.0,
        "min_confidence": 70.0,
        "trail_atr_mult": 1.5,
        "min_profit_factor_oos": 1.15,
        "min_trades_train": 12,
        "min_trades_oos": 8,
    },
    "🔥 Berserker / Full Tiburón": {
        "base_risk_pct": 2.5,
        "max_risk_pct": 4.5,
        "max_leverage": 8.0,
        "max_total_open_risk_pct": 14.0,
        "daily_loss_limit_pct": 7.0,
        "min_rr": 1.8,
        "min_confidence": 65.0,
        "trail_atr_mult": 1.3,
        "min_profit_factor_oos": 1.10,
        "min_trades_train": 10,
        "min_trades_oos": 6,
    },
    "🛡️ Conservador Institucional": {
        "base_risk_pct": 1.0,
        "max_risk_pct": 2.0,
        "max_leverage": 3.0,
        "max_total_open_risk_pct": 8.0,
        "daily_loss_limit_pct": 4.0,
        "min_rr": 2.0,
        "min_confidence": 70.0,
        "trail_atr_mult": 1.5,
        "min_profit_factor_oos": 1.20,
        "min_trades_train": 15,
        "min_trades_oos": 10,
    },
}

def apply_requests(broker, cfg, save, calibrate, log):
    token = os.getenv("PANEL_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not token or "/" not in repo:
        return []
    api = "https://api.github.com/repos/" + repo + "/issues"
    headers = {"Authorization": "Bearer " + token}
    response = requests.get(api, headers=headers, params={"state":"open", "creator":repo.split("/")[0], "per_page":100}, timeout=8)
    response.raise_for_status()
    done = []
    for issue in reversed(response.json()):
        title = issue["title"]
        if "pull_request" in issue or issue["user"]["login"].lower() != repo.split("/")[0].lower() or not title.startswith("Paper / "):
            continue
        ref = str(issue["number"])
        if cfg.get("panel_result", {}).get("id", 0) >= issue["number"]:
            done.append((api+"/"+ref, headers))
            continue
        age = datetime.now(timezone.utc) - datetime.fromisoformat(issue["created_at"].replace("Z","+00:00"))
        parts = title.split(" / ")
        if age.total_seconds() > 600:
            result = "Solicitud vencida"
        elif len(parts)==3 and parts[1]=="Perfil" and parts[2] in PRESETS:
            vals = PRESETS[parts[2]]
            risk = cfg.setdefault("risk_config", {})
            for key in ("base_risk_pct","max_risk_pct","max_leverage","max_total_open_risk_pct","min_rr","min_confidence","trail_atr_mult"):
                risk[key] = vals[key]
            risk["daily_loss_halt_pct"] = vals["daily_loss_limit_pct"]
            risk["max_net_crypto"] = 4 if vals["max_leverage"] >= 5 else 3
            wf = cfg.setdefault("wf_config", {})
            for key in ("min_profit_factor_oos","min_trades_train","min_trades_oos","trail_atr_mult"):
                wf[key] = vals[key]
            cfg["preset_name"] = parts[2]
            save(cfg)
            cfg.update(calibrate(broker,cfg,interval="15m",force=True))
            result = "Perfil aplicado: " + parts[2]
        elif len(parts)==5 and parts[1]=="Cerrar":
            pos = next((p for p in broker.state.open_positions if p["id"]==parts[2] and p["entry_time"]==parts[3] and p["symbol"]==parts[4]),None)
            if pos and pos.get("broker_mode","").startswith("Paper Trading"):
                result = broker.close_position_by_symbol(pos["symbol"],reason="Cierre manual desde el panel")
            else:
                result = "La posición paper ya no está abierta"
        else:
            result = "Solicitud no reconocida"
        cfg["panel_result"] = {"id":issue["number"],"message":result}
        save(cfg)
        log(f"Panel #{ref}: {result}")
        done.append((api+"/"+ref, headers))
    return done

def acknowledge(done):
    for url, headers in done:
        requests.patch(url, headers=headers, json={"state":"closed"},timeout=8).raise_for_status()
