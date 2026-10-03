// §49/§52 workflow.js：第二条生产链已删除（方案§49「最终全部调用 WorkflowRun」）。
// 旧 compose() 同步阻塞链 / renderResult() 结果页 / platformAspectSync 旧表单
// 联动全部移除——生产链唯一入口 = POST /api/workflows（create.js）。
//
// 本文件保留为空壳说明（script 引用兼容），不再承载任何业务逻辑。

Object.assign(window, {});
