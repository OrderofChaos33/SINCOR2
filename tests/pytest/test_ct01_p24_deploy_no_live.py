"""CT-01 safety gates: P24 is local-simulation-only; fee conversion stays held."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2].resolve()
SCRIPT = ROOT / "onchain" / "script" / "07_DeployP24.s.sol"
RUNBOOK = ROOT / "onchain" / "DEPLOYING.md"
CANONICAL_MANIFEST = ROOT / "onchain" / "deployments" / "base-sepolia-p24.json"
TASK_INVENTORY = ROOT / "TASK_DECOMPOSITION.json"
TASK_GENERATOR = ROOT / "gen_tasks.py"
IMPORT_RE = re.compile(r"^\s*import\s+(?:[^;]*?\s+from\s+)?[\"']([^\"']+)[\"']\s*;", re.M)
FORBIDDEN_P24_EFFECTS = (
    re.compile(r"\bvm\.(?:startBroadcast|stopBroadcast|broadcast|rememberKey|sign|writeJson|writeFile|serialize\w*|ffi)\s*\("),
    re.compile(r"\bvm\.env(?:Uint|Bytes32)\s*\("),
    re.compile(r"\bvm\.(?:createFork|createSelectFork|selectFork|rollFork|rpc|rpcUrl|transact)\s*\("),
    re.compile(r"\bvm\.env(?:String|Bytes|Int|Bool)\s*\("),
    re.compile(r"\b(?:sendRawTransaction|sendTransaction|eth_sendRawTransaction)\b"),
    re.compile(r"\b(?:PRIVATE_KEY|DEPLOYER_PRIVATE_KEY|P24_DEPLOYER_KEY)\b"),
    re.compile(r"--(?:broadcast|rpc-url|rpc|fork-url|fork-block-number|verify)\b"),
    re.compile(r"\b(?:writeJson|writeFile)\s*\("),
)


def _p24_runbook_section() -> str:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    return runbook.split("## P24 creator-issuance stack", maxsplit=1)[1]


def _local_solidity_import_closure(entrypoint: Path) -> dict[Path, str]:
    """Resolve all project-local Solidity imports; fail on unknown imports."""
    root = ROOT.resolve()
    pending = [entrypoint.resolve()]
    closure: dict[Path, str] = {}

    while pending:
        current = pending.pop()
        if current in closure:
            continue
        current.relative_to(root)
        source = current.read_text(encoding="utf-8")
        closure[current] = source
        for import_path in IMPORT_RE.findall(source):
            # forge-std is the test/script cheatcode interface, not a project
            # contract. Any other non-relative import requires explicit review.
            if import_path.startswith("forge-std/"):
                continue
            if not import_path.startswith(("./", "../")):
                raise AssertionError(f"unreviewed non-local Solidity import: {import_path}")
            target = (current.parent / import_path).resolve()
            target.relative_to(root)
            if not target.is_file():
                raise AssertionError(f"missing Solidity import: {current} -> {import_path}")
            pending.append(target)

    return closure


def _p24_forbidden_effects(source: str) -> list[str]:
    return [pattern.pattern for pattern in FORBIDDEN_P24_EFFECTS if pattern.search(source)]


def test_p24_local_import_closure_has_no_signer_broadcast_or_external_write_path():
    closure = _local_solidity_import_closure(SCRIPT)
    expected_contracts = {
        ROOT / "onchain/src/p24/ContentPolicyGuard.sol",
        ROOT / "onchain/src/p24/CreatorTokenFactory.sol",
        ROOT / "onchain/src/p24/FeeSplitDistributor.sol",
        ROOT / "onchain/src/p24/RevenueAccrual.sol",
    }
    assert expected_contracts <= closure.keys()

    violations = {
        f"{path.relative_to(ROOT)}: {pattern}"
        for path, source in closure.items()
        for pattern in _p24_forbidden_effects(source)
    }
    assert not violations, "P24 deploy import closure contains live-effect primitives: " + "; ".join(sorted(violations))

    script_source = closure[SCRIPT.resolve()]
    assert "SIMULATION ONLY" in script_source
    assert "NOT ON-CHAIN" in script_source
    assert "no durable artifact" in script_source
    assert not CANONICAL_MANIFEST.exists()


def test_p24_effect_scanner_detects_known_broadcast_primitives():
    assert _p24_forbidden_effects("vm.startBroadcast();")
    assert _p24_forbidden_effects("vm.writeJson(serialized, path);")
    assert _p24_forbidden_effects("vm.envUint('PRIVATE_KEY');")
    for source in (
        "vm.createFork('rpc');",
        "vm.createSelectFork('rpc');",
        "vm.selectFork(forkId);",
        "vm.rollFork(blockNumber);",
        "vm.rpc('eth_chainId', params);",
        "vm.rpcUrl('base-sepolia');",
        "vm.transact(txHash);",
        "forge script --fork-url https://example.invalid",
        "forge script --fork-block-number 123",
        "forge script --rpc-url https://example.invalid",
        "forge script --rpc https://example.invalid",
    ):
        assert _p24_forbidden_effects(source), source
    assert not _p24_forbidden_effects("new CreatorTokenFactory(admin);")
    assert not _p24_forbidden_effects('vm.envOr("P24_TREASURY", DEFAULT_TREASURY);')


def test_p24_runbook_is_simulation_only_and_has_no_deployment_record():
    section = _p24_runbook_section().lower()

    for forbidden in (
        "--broadcast",
        "deployer_private_key",
        "--verify",
        "base-sepolia-p24.json",
        "addresses deployed",
        "addresses are deployed",
        "simulation addresses are deployed",
        "simulated addresses are deployed",
        "deployed addresses",
    ):
        assert forbidden not in section

    assert "ephemeral simulation addresses, not on-chain\ndeployment evidence" in section
    assert "no p24 deployment manifest" in section
    assert not CANONICAL_MANIFEST.exists()


def test_p24_task_inventory_keeps_live_actions_on_explicit_owner_hold():
    tasks = json.loads(TASK_INVENTORY.read_text(encoding="utf-8"))["tasks"]
    p24_tasks = [task for task in tasks if task.get("parent_item") == 8]
    by_id = {task["id"]: task for task in p24_tasks}

    assert {
        "T0048", "T0049", "T0050", "T0880", "T0881", "T0882", "T0883",
        "T0884", "T0885", "T0886", "T0887",
    } <= by_id.keys()
    for task_id in ("T0048", "T0049", "T0050", "T0880", "T0883"):
        task = by_id[task_id]
        assert task["status"] == "blocked"
        assert "separate explicit owner authorization" in task["blocked_by"].lower()

    for task_id in ("T0881", "T0882", "T0884", "T0885", "T0886", "T0887"):
        task = by_id[task_id]
        assert task["status"] == "pending"
        assert "local simulation" in f"{task['title']} {task['scope']}".lower()
        assert task["blocked_by"] is None
    assert "import closure" in f"{by_id['T0881']['title']} {by_id['T0881']['scope']}".lower()
    assert "in-memory foundry test" in by_id["T0882"]["scope"].lower()
    assert "no broadcast" in by_id["T0882"]["scope"].lower()

    all_p24_task_text = " ".join(str(value) for task in p24_tasks for value in task.values()).lower()
    for stale_instruction in (
        "p24 rehearsal: deploy to sepolia",
        "p24 rehearsal: verify contracts",
        "p24 rehearsal: fund rehearsal wallet",
        "founder must authorize contract broadcast + fund deployer wallet",
    ):
        assert stale_instruction not in all_p24_task_text

    milestone = next(task for task in tasks if task["title"] == "Milestone: Base Sepolia deploy ceremony")
    assert "local-simulation-only" in milestone["scope"].lower()
    assert "p24 has no deployment evidence" in milestone["acceptance"].lower()


def test_p24_foundry_test_executes_the_deploy_script_only_in_local_test_context():
    source = (ROOT / "onchain/test/p24/P24Deploy.t.sol").read_text(encoding="utf-8")
    assert 'import {DeployP24} from "../../script/07_DeployP24.s.sol";' in source
    assert "function test_DeployScriptRunCreatesOnlyLocalTestContracts()" in source
    assert "script.run()" in source
    assert "vm.setEnv(\"P24_ADMIN\"" in source
    assert "local test EVM" in source
    assert "vm.startBroadcast" not in source
    assert "vm.broadcast" not in source


def test_legacy_generator_is_preview_only_and_cannot_overwrite_canonical_ledger(tmp_path):
    canonical_before = TASK_INVENTORY.read_bytes()

    default_run = subprocess.run(
        [sys.executable, str(TASK_GENERATOR)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert default_run.returncode != 0
    assert not (tmp_path / "TASK_DECOMPOSITION.json").exists()

    rejected_overwrite = subprocess.run(
        [sys.executable, str(TASK_GENERATOR), "--preview-output", str(TASK_INVENTORY)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected_overwrite.returncode != 0
    assert "cannot overwrite" in rejected_overwrite.stderr.lower()
    assert TASK_INVENTORY.read_bytes() == canonical_before

    preview_path = tmp_path / "task-preview.json"
    preview = subprocess.run(
        [sys.executable, str(TASK_GENERATOR), "--preview-output", str(preview_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert preview.returncode == 0, preview.stderr
    preview_data = json.loads(preview_path.read_text(encoding="utf-8"))
    assert preview_data["preview_only"] is True
    preview_tasks = preview_data["tasks"]
    assert any(
        task.get("parent_item") == 8 and "authorization hold" in task["title"].lower()
        for task in preview_tasks
    )
    fee_tasks = [task for task in preview_tasks if task.get("parent_item") == 32]
    fee_text = " ".join(str(value) for task in fee_tasks for value in task.values()).lower()
    assert "forwarder key into secure vault" not in fee_text
    assert "arming ceremony: armed=true" not in fee_text
    assert TASK_INVENTORY.read_bytes() == canonical_before


def test_fee_conversion_task_ledger_contains_no_key_or_arming_procedure():
    tasks = json.loads(TASK_INVENTORY.read_text(encoding="utf-8"))["tasks"]
    fee_tasks = [task for task in tasks if task.get("parent_item") == 32]
    fee_text = " ".join(str(value) for task in fee_tasks for value in task.values()).lower()
    assert "forwarder key into secure vault" not in fee_text
    assert "arming ceremony: armed=true" not in fee_text
    assert "canonical fee-conversion authorization design hold" in fee_text
    assert "legacy armed flags cannot authorize live conversion" in fee_text


def test_treasury_policy_comment_and_config_are_explicitly_fail_closed():
    source = (ROOT / "src/sincor2/treasury_policy.py").read_text(encoding="utf-8").lower()
    assert "always false" in source
    assert "legacy armed flags cannot enable execution" in source
    assert "until an executor is armed" not in source


def test_operator_fee_conversion_docs_do_not_prescribe_key_or_arming_steps():
    checklist = (ROOT / "docs/ops/PRODUCTION_DEPLOY_CHECKLIST.md").read_text(encoding="utf-8")
    fee_section = checklist.split("### 3d. Fee executor", maxsplit=1)[1].split("## Phase 4", maxsplit=1)[0]
    assert "Do not provision/custody a forwarder key" in fee_section
    assert "SINCOR_FEE_EXECUTOR_ARMED" in checklist
    assert "not authorization" in checklist.lower()
    assert "SINCOR_FEE_EXECUTOR_ARMED=true" not in fee_section

    historical = (ROOT / "docs/ops/FEE_EXECUTOR_FORKSIM_READINESS.md").read_text(encoding="utf-8")
    current_disposition = historical.split("## Current disposition", maxsplit=1)[1]
    normalized_disposition = " ".join(current_disposition.split())
    assert "not actionable" in current_disposition.lower()
    assert "Do not create or seed a pool" in normalized_disposition
    assert "Only after all gates pass, separately authorize `armed=True`." not in historical

    architecture = (ROOT / "docs/architecture/CANONICAL_PATHS.md").read_text(encoding="utf-8").lower()
    assert "until an executor is armed" not in architecture
    assert "legacy `armed` setting" in architecture
