"""
E2E 自动化测试套件 (Playwright + Chromium)

参考 gstack 浏览器自动化体系建立的三层 E2E 测试：
- test_dashboard.py   功能测试（参考 gstack /qa 的 diff-aware 模式）
- test_visual.py      视觉回归测试（参考 gstack screenshot + diff 模式）
- test_performance.py  性能基准测试（参考 gstack /benchmark）
- test_a11y.py        无障碍基础检查（参考 gstack /design-review a11y 清单）
- test_scenarios.py   YAML 场景驱动测试（参考 AutoCLI 的声明式 YAML Pipeline 模式）

参考 AutoCLI (nashsu/AutoCLI, github.com/nashsu/AutoCLI) 的设计模式:
- 声明式 YAML Pipeline  → scenarios/dashboard.yaml
- explore API 发现      → helpers.explore_page() 自动分析页面结构
- Chrome Extension 可视化元素选择 → 在 YAML selector 中以 "text=..." 模式体现
- cascade 认证探测      → dashboard 的 Consul/Mock 自动降级

运行：
  E2E_HEADED=true pytest tests/e2e/ -v          # headed 调试
  pytest tests/e2e/ -v                           # headless 运行
  pytest tests/e2e/ -v --update-snapshots        # 更新视觉基线
  pytest tests/e2e/test_scenarios.py -v          # YAML 场景测试
"""
