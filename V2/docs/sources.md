# 资料来源

`docs/product/` 描述的是本项目测试产品的明确规则，不代表任何真实电商平台。外部资料只用于实现技术组件：

- PostgreSQL：https://www.postgresql.org/docs/
- pgvector：https://github.com/pgvector/pgvector
- Gemini Embeddings：https://ai.google.dev/gemini-api/docs/embeddings
- LangGraph：https://docs.langchain.com/oss/python/langgraph/
- LangSmith：https://docs.smith.langchain.com/

当前产品规则与故障处理以 `simulator/services/`、`backend/app/` 的实际业务实现为准；具体对象结论还需要当前、同范围的业务证据。`building-plan-V2.md` 是建设背景，不能单独证明规划功能已经支持，Evaluation Case 也不作为产品能力依据。

原始知识的主来源、格式、当前/历史状态和旧文件迁移关系见 [产品知识来源目录](product-catalog.md)。本次只整理知识内容，没有改变导入、检索或 Agent 工作流，也没有运行 Evaluation；多格式文件仍需后续基础建设接入和重新索引。
