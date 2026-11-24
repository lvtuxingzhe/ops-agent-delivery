set +e
python - <<'PY'
# -*- coding: utf-8 -*-
import json, os, subprocess, datetime, sys

NAMESPACES = [n for n in os.environ.get("CAPTURE_NAMESPACES", "").split(",") if n]
if not NAMESPACES:
    NAMESPACES = ["eval-hard-key", "eval-hard-quota", "eval-hard-node", "eval-hard-dual", "eval-hard-inject"]

def run(args):
    try:
        out = subprocess.check_output(args, stderr=subprocess.STDOUT)
        return out
    except subprocess.CalledProcessError as exc:
        return exc.output or ""

def get_json(args):
    raw = run(args)
    try:
        return json.loads(raw)
    except Exception:
        return {}

def cond(raw):
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if key in ("waiting", "terminated", "running"):
            detail = {"state": key}
            for field in ("reason", "message", "exitCode", "signal", "startedAt", "finishedAt"):
                if value.get(field) is not None:
                    detail[field] = value.get(field)
            out.update(detail)
    return out

def summarize_containers(spec):
    rows = []
    for c in spec.get("containers") or []:
        env = []
        for item in c.get("env") or []:
            ref = item.get("valueFrom") or {}
            env.append({
                "name": item.get("name"),
                "value": item.get("value"),
                "config_map_key_ref": ref.get("configMapKeyRef"),
                "secret_key_ref": ref.get("secretKeyRef"),
            })
        ports = [
            {"name": p.get("name"), "container_port": p.get("containerPort"), "protocol": p.get("protocol")}
            for p in c.get("ports") or []
        ]
        probes = {}
        for probe_name in ("readinessProbe", "livenessProbe", "startupProbe"):
            probe = c.get(probe_name) or {}
            if probe:
                probes[probe_name] = {
                    "http_get": probe.get("httpGet"),
                    "tcp_socket": probe.get("tcpSocket"),
                    "exec": probe.get("exec"),
                    "initial_delay_seconds": probe.get("initialDelaySeconds"),
                    "period_seconds": probe.get("periodSeconds"),
                }
        rows.append({
            "name": c.get("name"),
            "image": c.get("image"),
            "command": c.get("command") or [],
            "args": c.get("args") or [],
            "env": env,
            "ports": ports,
            "probes": probes,
            "resources": c.get("resources") or {},
            "volume_mounts": [
                {"name": m.get("name"), "mount_path": m.get("mountPath"), "sub_path": m.get("subPath")}
                for m in c.get("volumeMounts") or []
            ],
        })
    return rows

def summarize_pod(pod):
    status = pod.get("status") or {}
    spec = pod.get("spec") or {}
    containers = []
    for cs in status.get("containerStatuses") or []:
        containers.append({
            "name": cs.get("name"),
            "ready": cs.get("ready"),
            "restart_count": cs.get("restartCount"),
            "image": cs.get("image"),
            "state": cond(cs.get("state")),
            "last_state": cond(cs.get("lastState")),
        })
    return {
        "name": pod.get("metadata", {}).get("name"),
        "phase": status.get("phase"),
        "node": spec.get("nodeName"),
        "labels": pod.get("metadata", {}).get("labels") or {},
        "pod_ip": status.get("podIP"),
        "node_selector": spec.get("nodeSelector") or {},
        "service_account": spec.get("serviceAccountName"),
        "volumes": [
            {"name": v.get("name"), "config_map": v.get("configMap"), "secret": v.get("secret"), "pvc": v.get("persistentVolumeClaim")}
            for v in spec.get("volumes") or []
        ],
        "spec_containers": summarize_containers(spec),
        "containers": containers,
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": c.get("message")}
            for c in status.get("conditions") or []
        ],
    }

def summarize_deploy(dep):
    status = dep.get("status") or {}
    spec = dep.get("spec") or {}
    return {
        "name": dep.get("metadata", {}).get("name"),
        "replicas": spec.get("replicas"),
        "ready_replicas": status.get("readyReplicas"),
        "available_replicas": status.get("availableReplicas"),
        "updated_replicas": status.get("updatedReplicas"),
        "selector": (spec.get("selector") or {}).get("matchLabels") or {},
        "template_labels": ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {},
        "template_node_selector": (((spec.get("template") or {}).get("spec") or {}).get("nodeSelector")) or {},
        "template_containers": summarize_containers((spec.get("template") or {}).get("spec") or {}),
        "template_volumes": [
            {"name": v.get("name"), "config_map": v.get("configMap"), "secret": v.get("secret"), "pvc": v.get("persistentVolumeClaim")}
            for v in (((spec.get("template") or {}).get("spec") or {}).get("volumes") or [])
        ],
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": c.get("message")}
            for c in status.get("conditions") or []
        ],
    }

def summarize_ns(ns):
    pods_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "pods", "-o", "json"])
    deps_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "deployments", "-o", "json"])
    svc_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "services", "-o", "json"])
    ep_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "endpoints", "-o", "json"])
    ev_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "events", "--sort-by=.lastTimestamp", "-o", "json"])
    pvc_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "pvc", "-o", "json"])
    cm_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "configmaps", "-o", "json"])
    quota_raw = get_json(["k3s", "kubectl", "-n", ns, "get", "resourcequotas", "-o", "json"])
    nodes_raw = get_json(["k3s", "kubectl", "get", "nodes", "-o", "json"])

    pods = [summarize_pod(item) for item in (pods_raw.get("items") or [])]
    logs = {}
    for pod in pods:
        for c in pod.get("containers") or []:
            name = c.get("name")
            for suffix, extra in (("", []), ("-previous", ["--previous"])):
                text = run(["k3s", "kubectl", "-n", ns, "logs", pod["name"], "-c", name, "--tail=200"] + extra)
                text = (text or "").decode("utf-8", "replace") if isinstance(text, str) else str(text)
                if text and not text.startswith("Error from server"):
                    logs["%s/%s%s" % (pod["name"], name, suffix)] = text[:3000]

    return {
        "namespace": ns,
        "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
        "pods": pods,
        "deployments": [summarize_deploy(item) for item in (deps_raw.get("items") or [])],
        "services": [
            {
                "name": item.get("metadata", {}).get("name"),
                "type": (item.get("spec") or {}).get("type"),
                "cluster_ip": (item.get("spec") or {}).get("clusterIP"),
                "selector": (item.get("spec") or {}).get("selector") or {},
                "ports": [
                    {"port": p.get("port"), "target_port": p.get("targetPort"), "protocol": p.get("protocol")}
                    for p in (item.get("spec") or {}).get("ports") or []
                ],
            }
            for item in (svc_raw.get("items") or [])
        ],
        "endpoints": [
            {
                "name": item.get("metadata", {}).get("name"),
                "subsets": [
                    {
                        "addresses": [a.get("ip") for a in subset.get("addresses") or []],
                        "ports": [{"port": p.get("port"), "protocol": p.get("protocol")} for p in subset.get("ports") or []],
                    }
                    for subset in item.get("subsets") or []
                ],
            }
            for item in (ep_raw.get("items") or [])
        ],
        "events": [
            {
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "involved_object": "%s/%s" % ((item.get("involvedObject") or {}).get("kind"), (item.get("involvedObject") or {}).get("name")),
                "count": item.get("count"),
                "last_timestamp": item.get("lastTimestamp"),
            }
            for item in (ev_raw.get("items") or [])
        ],
        "pvc": [
            {
                "name": item.get("metadata", {}).get("name"),
                "phase": (item.get("status") or {}).get("phase"),
                "storage_class": (item.get("spec") or {}).get("storageClassName"),
                "requested": (((item.get("spec") or {}).get("resources") or {}).get("requests") or {}).get("storage"),
            }
            for item in (pvc_raw.get("items") or [])
        ],
        "configmaps": [
            {
                "name": item.get("metadata", {}).get("name"),
                "keys": sorted((item.get("data") or {}).keys()),
            }
            for item in (cm_raw.get("items") or [])
        ],
        "resource_quotas": [
            {
                "name": item.get("metadata", {}).get("name"),
                "hard": (item.get("spec") or {}).get("hard") or {},
                "used": (item.get("status") or {}).get("used") or {},
            }
            for item in (quota_raw.get("items") or [])
        ],
        "nodes": [
            {
                "name": item.get("metadata", {}).get("name"),
                "conditions": [
                    {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": c.get("message")}
                    for c in (item.get("status") or {}).get("conditions") or []
                ],
                "allocatable": (item.get("status") or {}).get("allocatable") or {},
            }
            for item in (nodes_raw.get("items") or [])
        ],
        "logs": logs,
    }

result = {}
for ns in NAMESPACES:
    result[ns] = summarize_ns(ns)
print(json.dumps(result, ensure_ascii=True, sort_keys=True))
PY
echo "CAPTURE_EXIT=$?"
