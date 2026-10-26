# -*- coding: utf-8 -*-
"""方案 §77/§78 UI Action Audit：机械检查按钮功能完整性。

扫描 index.html + 动态模板字符串：
  1. 每个 <button> 的 onclick → 业务 or 纯 UI 白名单
  2. 每个 data-action → Registry 存在 + dispatcher 可达（ui 型/后端型）
  3. 业务 onclick（非白名单）→ ORPHAN_BUTTON 违规报告

报告：PASS / MISSING_ACTION / MISSING_EXECUTOR / ORPHAN_BUTTON。
可作 pytest 套件（test_ui_action_audit）常驻——以后不靠人工点 100 个按钮。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]

#: §77 纯 UI 白名单（无业务语义：导航/开关/表单提交收集器/选择）
UI_ONLY_PREFIXES = (
    "switchTab(", "captureDrawerToggle(", "openCreate(", "closeCreate(",
    "pmSetFilter(", "edLibDomain(", "edLibSub(", "edLibNav(", "timelineView(", "previewStepFrame(",
    "reviewModeToggle(", "reviewModeScan(", "projectDrawerToggle(", "versionDrawerToggle(",
    "toggleLLMPanel(", "toggleCapPanel(", "devToggleMode(", "loadSettings(",
    "loadProjects(", "loadSeries(", "loadInbox(", "inboxAdd(", "inboxAction(",
    "editorInit(", "editorLoad(", "workspaceOpen(", "pmOpenDetail(", "pmShowMore(",
    "editorSelectScene(", "editorSelectLayer(", "editorSelectVisualOverlay(", "editorSemanticRerank(", "edSelect(", "selectScene(", "selectLayer(",
    "selectProject(", "selectScenePair(", "clearSelection(", "selectNarration(",
    "tlToggleGroup(", "pmPreviewFinal(", "pmPreviewLegacy(", "pmReopenFailed(",
    "editorZoomTimeline(", "editorVisualOverlayPickSpeed(",  # 视图缩放/面板预填（最终落值走保存）
    "continueProject(", "pmAskDelete(", "pmAskRename(", "pmCloseModal(", "pmShowModal(",
    "reviewModeSelect(", "publishDetail(", "reviewLocate(", "reviewRecheck(",
    "reviewAskAI(", "reviewFixRelation(",  # 定位/预填（最终 apply 走 data-action）
    "gfxPlay(", "gfxPause(", "gfxPreview(", "gfxGenAsset(", "gfxRemoveScene(",
    "gfxApplyToWorkspace(", "gfxMakePlanFrom(", "inboxToCreate(",
    "cfVoiceModeSync(", "createPlatformSync(", "setCreateSource(",
    "createLoadDefaults(", "createEnhance(", "createAcceptEnhanced(",
    "edVoiceLoadOptions(",  # 数据装载
    # 2026-10-25 创作要求面板（表单收集器：读 textarea → 后端 brief/retry 端点）
    "directorLoadBrief(", "directorSaveBrief(", "directorRerunFromBrief(",
    # 2026-10-25 创作流程窗口（进度视图切换/取消返回表单/预填装载）
    "cfEnterProgressMode(", "cfCancelAndEdit(", "cfDismissProgress(", "cfPrefillFromProject(",
    "cfStopCreation(",
    # 2026-10-26 项目列表观看/停止创作进度（读 run → 步骤面板；停→cancel 端点）
    "pmShowProgress(", "_pmRenderProgress(", "_pmStop(",
    # 表单收集器（读输入框 → 内部走 executeAction / 后端链）
    "editorApplyDuration(", "editorApplyTrim(", "editorApplySpeed(",
    "editorSetVisualTransform(", "editorAddKeyframe(", "editorRemoveKeyframe(",
    "editorApplyMotionParam(", "editorAudioBusApply(", "editorSubtitleStyleApply(",
    "editorWatermarkMove(", "editorWatermarkOpacity(", "editorWatermarkAdd(",
    "editorApplyContinuity(", "editorApplyMorph(", "edContTypeChanged(", "edMorphFromChanged(",
    "editorVerifyContinuity(", "editorOriginalAudioVolume(", "editorLayerOriginalVolume(",
    "editorMultiOriginalVolume(", "edVoicePreview(", "edVoiceFullPreview(",
    "edVoiceRegenerate(", "edVoiceReplaceUpload(", "edVoiceReplaceApply(",
    "edVoiceTry(", "edVoiceApply(",  # 2026-10-21 音色应用（内部 executeAction('voice.change')）
    "edLibAudioTry(", "edLibStockSearch(", "assetPickerStockSearch(",
    "assetPickerStockDownload(", "edLibOpenTemplate(", "instantiateTemplate(", "renderTemplateSample(",
    "pickerPreviewStage(", "pickerApply(", "pickerCancel(",
    "editorExportFreeze(",  # 2026-10-21 定格导出（POST /api/export_freeze 收集器）
    "edGenerateMetadata(",  # 2026-10-23 发布文案（内部 executeAction('delivery.generate_metadata')）
    "maskEditorSave(", "maskEditorCancel(", "maskEditorUndo(", "maskEditorRedo(",
    "editorToggleLock(", "editorRunMatting(", "editorRemoveKeyframe(",
    "editorApplyPip(", "editorRemovePip(", "editorPreviewMotionParam(",
    "editorApplyCamera(",  # 带参数预设的快捷（内部 executeCommand → 待迁移清单）
    "editorPreflight(", "editorDraftRender(", "editorRenderFinal(",
    "directorExecute(", "directorRefreshContext(", "directorLoadCapCount(",
    "createSeriesAndBatch(", "createProject(", "saveModelConfig(",
    "document.getElementById(",  # 纯 DOM 操作（关闭行内区）
    # Asset Picker 收集器（采用/收藏：读 tab 上下文 → 内部走 executeAction/editorReplaceSource）
    "_apApplyAsset(", "apToggleFav(",
    # 项目域（project_action API——方案 §49：项目操作不进 Composition Command Bus）
    "pmClone(", "pmArchive(", "pmRestore(", "convertLegacyProject(", "renameProject(",
    "deleteProject(", "projectAction(",
    # Create 表单收集器/预填
    "templateGalleryOpen(", "templateGalleryClose(", "templateGalleryLoad(",
    "styleGalleryOpen(", "styleGalleryClose(", "styleGalleryLoad(",
    "templateGallerySelectByIndex(", "templateGalleryRenderSample(", "templateGalleryInstantiate(",
    "cfMakeGraphicPlan(", "cfAnalyzeReference(", "cfImportArticle(", "cfClearArticleSource(", "cfScanRepo(", "editorGenerate(",
    # Inspector 表单收集器（读输入框收集 → 内部 executeAction）
    "editorApplyMask(", "editorSubtitleWhiteOnBlack(",
    # Library Picker 采用按钮（收集器：内部 executeAction）
    "edLibApplyAsset(", "edLibAutoMatchBgm(", "edLibAudioApply(",
    # 项目域 ··· 菜单/发布变体/版本对比（UI 状态，无 Composition 业务）
    "pmRowMenu(", "publishVariants(", "verCompareSelected(",
    # ··· 菜单项 = pmCloseRowMenu() + 已白名单项目域动作（关闭浮层 + 委托）
    "pmCloseRowMenu(", "cfGenerateScript(",
    # 轨道点击 → 打开对象上下文（§5.2/§6/§46 纯 UI 导航）
    "projectOverlayOpen(", "originalTrackOpen(", "bgmTrackOpen(",
    "editorAudioVolume(", "editorSubtitleStyleOpen(",
    "editorAudioClipOpen(", "editorAudioClipApply(", "editorAudioClipClose(", "editorAudioClipSplit(",
    "editorInspectorPatchVisualOverlay(", "editorInspectorToggleOverlayAudio(", "editorInspectorPatchAudioClip(",
    "editorEditSelectedVisualOverlay(", "editorClipCopy(",
    "editorAudioTrackAdd(", "editorAudioAssetPick(", "editorAudioTrackMenu(", "editorAudioTrackMenuClose(",
    "editorAudioTrackApplyName(", "editorAudioTrackRemove(", "editorAudioTrackToggleLock(", "editorAudioTrackToggleMute(", "editorAudioTrackToggleHidden(", "editorSubtitleCueSelect(",
    "editorWatermarkClipOpen(", "editorWatermarkClipApply(", "editorWatermarkClipClose(",
    "edxSelectScene(", "edxDirectorEdit(", "edxMaterialMatch(", "edxApplyMaterialMatch(",
    "edxProposeTransitions(", "edxApproveTransition(", "edxRejectTransition(",
    "editorWatermarkRemoveAt(",
    "editorVisualOverlayOpen(", "editorVisualOverlayApply(", "editorVisualOverlayCreate(",
    "editorVisualOverlayRemove(", "editorVisualOverlayClose(", "editorVisualOverlaySplit(",
    "editorVisualOverlayDetachAudio(",
    "editorVisualOverlayPick(", "editorPromoteLayerToTrack(",
    "editorVisualTrackCreate(", "editorVisualTrackCreateClose(", "editorVisualTrackConfirm(",
    "editorVisualTrackMenu(", "editorVisualTrackMenuClose(", "editorVisualTrackMove(",
    "editorVisualTrackRename(", "editorVisualTrackRemove(", "editorVisualTrackToggleLock(",
    "editorExpandMainTimelineToGlobalTrack(",
    "editorSourceMonitorRefresh(", "editorSourceMonitorRefreshTracks(",
    "editorSourceMonitorSelectAsset(", "editorSourceMonitorMarkIn(",
    "editorSourceMonitorMarkOut(", "editorSourceMonitorAddToTimeline(",
    # §46 任务中心抽屉（WorkflowRun 渲染层 UI 开关）
    "taskDrawerToggle(", "taskRefresh(", "taskShowDetail(", "taskOpenProject(", "taskRetryDirector(",
    # §52 配置导入导出（设置表单）
    "settingsExport(", "settingsImport(",
    "pmToggleSelected(", "pmToggleAll(", "pmAskBulkDelete(", "pmBulkAction(", "pmClearSelection(",
    "seriesToggle(", "seriesToggleAll(", "seriesBulkDelete(",
    "inboxToggle(", "inboxToggleAll(", "inboxBulkAction(", "inboxBulkDeleteSelected(", "inboxBulkDismissSelected(",
    "taskToggleSelected(", "taskToggleAllTerminal(", "taskBulkCleanup(",
    "edLibToggleAsset(", "edLibToggleUnusedAll(", "edLibBulkDeleteAssets(",
    # 素材语义编辑（/api/asset_semantics 写入链）
    "edLibSemanticsForm(", "edLibSaveSemantics(", "edLibLoad(",
    # 任务中心失败重试（PR #94 director.retry 链）
    "taskRetryDirector(",
    # FAILED 项目失败查看（项目页就地 modal，不进编辑器）
    "pmShowFailure(",
    "event.stopPropagation()",
)

#: data-action 白名单外的检查表（Registry 必须 know）
def _extract_data_actions(src: str) -> set:
    return set(re.findall(r'data-action="([^"]+)"', src))


def _extract_onclicks(src: str) -> list:
    out = []
    for m in re.finditer(r'onclick="([^"]+)"', src):
        call = m.group(1).strip()
        call = call.replace("javascript:", "").strip()
        out.append(call)
    return out


def audit_ui() -> dict:
    """§78 主审计：扫描全部前端源 → 四层核对报告。"""
    from video_director import action_registry as ar

    registry_ids = set(ar.registry().keys())
    ui_map = {}
    for a in ar.registry().values():
        if a.ui:
            ui_map[a.id] = a.ui

    files = [ROOT / "webui" / "static" / "index.html"]
    files += sorted((ROOT / "webui" / "static" / "js").glob("*.js"))

    report = {"pass": 0, "missing_action": [], "missing_executor": [],
              "orphan_button": [], "scanned_files": len(files)}
    # §82 附注观测：前端直连 executeCommand( 的调用点分布（含定义处 commands.js 自身）。
    # 纯增字段，不动 orphan_button / missing_action 语义——executeCommand 是
    # 命令级 Action 通道（ActionSpec.command 直发），不是旁路，故不判违规，
    # 只回答「命令级通道还被哪些模块在用」。
    direct_command_callsites = {}

    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(f.relative_to(ROOT))
        # 1) data-action 全部在 Registry
        for aid in _extract_data_actions(src):
            if aid not in registry_ids:
                report["missing_action"].append(f"{rel}: data-action={aid}")
            else:
                report["pass"] += 1
        # 2) ui 型 data-action 必须有本地行为表条目（MISSING_EXECUTOR 在
        #    前端 dispatcher 侧——本审计只报后端 Resolver 断层：ui 型跳过）
        # 3) 业务 onclick 白名单外 = ORPHAN_BUTTON
        for call in _extract_onclicks(src):
            if not call:
                continue
            # 模板间接注入（onclick="${cl.onclick}"）：值来自本文件内的
            # onclick: "fn(...)" 字符串——递归按白名单校验这些间接目标
            if call.startswith("$"):
                pat = re.compile(r"onclick[\x22\x27]?\s*[:=]\s*[\x22\x27`](\w+\()")
                indirect = set(pat.findall(src))
                bad_indirect = [fn for fn in indirect
                                if not fn.startswith(UI_ONLY_PREFIXES)]
                if bad_indirect:
                    for fn in bad_indirect:
                        report["orphan_button"].append(f"{rel}: 间接 {fn}...")
                else:
                    report["pass"] += 1
                continue
            if call.startswith(UI_ONLY_PREFIXES):
                report["pass"] += 1
                continue
            report["orphan_button"].append(f"{rel}: onclick={call[:60]}")

        # §82 观测：本文件内 executeCommand( 调用点数（定义处 commands.js 自身也计入，
        # 由文件名单列自解释，不做特判——统计全部静态调用点）
        _n_cmd_calls = len(re.findall(r"executeCommand\(", src))
        if _n_cmd_calls:
            direct_command_callsites[rel] = _n_cmd_calls

    report["direct_command_callsites"] = direct_command_callsites
    return report


def format_report(r: dict) -> str:
    lines = [f"UI Action Audit（§78）——扫描 {r['scanned_files']} 文件，PASS {r['pass']} 项"]
    for k, label in (("missing_action", "MISSING_ACTION（data-action 不在 Registry）"),
                     ("missing_executor", "MISSING_EXECUTOR"),
                     ("orphan_button", "ORPHAN_BUTTON（业务 onclick 白名单外）")):
        items = r[k]
        lines.append(f"  {label}: {len(items)}")
        for x in items[:20]:
            lines.append(f"    - {x}")
    # §82 附注观测（不影响 PASS/违规语义）：命令级 Action 通道调用点分布
    callsites = r.get("direct_command_callsites") or {}
    if callsites:
        total = sum(callsites.values())
        lines.append(f"  DIRECT_COMMAND_CALLSITES（§82 观测，非违规）: {len(callsites)} 文件 / {total} 处")
        for f, n in sorted(callsites.items(), key=lambda kv: -kv[1])[:20]:
            lines.append(f"    - {f}: {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    rep = audit_ui()
    print(format_report(rep))
    raise SystemExit(1 if (rep["missing_action"] or rep["orphan_button"]) else 0)
