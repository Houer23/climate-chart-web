"""DrawClimateChartWeb 的 Web 服务层。

模块划分
--------
* ``rendering``       渲染适配层：让 ``core`` 的绘图/表格能力支持「内存产物」
* ``schema``          配置 Schema：字段类型 / 枚举 / 范围 / 说明 / 分组
* ``config_service``  配置服务：读取 / 校验 / 保存修改 / 另存（全量 · 继承差量）
* ``task_runner``     渲染任务：后台执行、串行锁、日志桥、保存与临时出图
* ``routes``          HTTP 路由与 API
* ``server``          HTTP 服务器（仅标准库 ``http.server``）
"""

__version__ = "1.0.0"
