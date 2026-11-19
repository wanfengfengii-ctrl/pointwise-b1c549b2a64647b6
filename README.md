# 带材巡检相机质检站

试验线班长为已登记的本地模拟相机开启录制轮。值班员边录制边查看近几帧的灰度计量，在交班时暂停并签发。接班质检员在原相机与录制会话关闭后，仍能从归档核对当班交付的原件及质检计量。

## 一次交班

1. 开轮时固定环容量、宽、高和单帧灰度阈值。容量和尺寸为正整数，阈值为非负整数。
2. 相机每步送一个带严格递增整数源序的完整 `bytes`。帧长为宽乘高，每字节是 0 至 255 的无符号灰度值。源序允许跳号。录满后成功写入复用最旧槽。
3. 值班员查看有效帧数、原字节灰度总量和超限帧数。每帧的字节和严格大于阈值时计一次超限。空窗三项为零。坏帧或重复源序被拒后仍可继续录制。
4. 暂停固定本轮高位，停止该轮写入并开一张待签交班单。同一相机可开新轮，原暂停轮仍可签发。重复暂停沿用原截点。
5. 以该轮及原截点申请签发。归档取暂停环的原序窗口，按冻结口径核定计量。空暂停窗允许留待签单，申请签发会报错。
6. 成果头、原帧与交班引用一起提交。写入或提交失败时仍显示待签，可在原会话以同截点重试。成功重投复领原号。
7. 结束相机与会话并重开归档。按轮在交班清单取成果号，按号领取固定原件、源序、形状、阈值及计量。位置、帧数、源序、摘要或复算不符时，领取整项报错。

## Python 接口

```python
from station.archive.issue import issue_stopped
from station.archive.store import ArchiveStore
from station.capture.source import CameraFrameEvent, SimulatedCamera
from station.handover.board import handover_board
from station.handover.receive import receive_stopped
from station.runs.session import RecordingSession

with RecordingSession(['line-cam']) as session, ArchiveStore('shift.sqlite3') as store:
    run = session.open_run('line-cam', capacity=2, width=2, height=1, threshold=5)
    with SimulatedCamera(session.capture, run.run_id) as camera:
        camera.step(CameraFrameEvent(10, bytes([0, 2])))
        camera.step(CameraFrameEvent(14, bytes([1, 4])))
        camera.step(CameraFrameEvent(20, bytes([3, 4])))
    values = session.live_quality(run.run_id)
    cutoff = session.pause_run(run.run_id)
    result_id = issue_stopped(session, store, run.run_id, cutoff)

with ArchiveStore('shift.sqlite3') as store:
    ticket, = handover_board(store, run.run_id)
    review = receive_stopped(store, run.run_id, ticket.result_id)
    assert ticket.result_id == result_id
    assert review.header.values == values
```

`session.append_frame` 和 `camera.step` 都返回含 `slot` 和 `high_water` 的接纳回执。`CaptureRecorder.slot_index` 提供按原序排列的当前槽号、当前源序和物理页地址，`read_page` 单次读取一个物理页。低层 `PageStore` 支持按具体页地址写入完整帧和读取该页。录制 API 按事件串行使用。`handover_board(store, run_id, session)` 可查看会话内的空清单和待签单。提供已签轮号时，也可在独立归档上调用清单。`station.archive.readback.read_stopped(store, result_id)` 提供直接按号领取。

未知轮、相机或成果报 `KeyError`。非法输入、设备已占用、阶段或截点不符及空窗签发报 `ValueError`。已关闭资源报 `RuntimeError`。领取坏证据报 `ArchiveIntegrityError`。SQLite 及归档故障钩子的异常会返回给调用者。`ArchiveStore` 支持可选 `after_frame(result_id, position)` 和 `before_commit(result_id)` 钩子，分别在事务内某条原件已写入时及交班引用写完后调用。钩子抛错将回滚本次事务。用独立 SQLite 连接观察提交可见性。重复打开归档会保留已提交成果、引用及合法用户修改。待签单和原环留在录制会话内。

## 回归与演示

需要 Python 3.11 及其标准库 SQLite 模块。在项目根目录执行标准完整回归。

```sh
python3 -B check_project.py
```

直接发现全部测试也可使用 `python3 -B -m unittest discover -s tests -t . -p 'test_*.py'`。`python3 -B demo.py` 使用临时目录，从 `examples/shift.json` 的模拟相机事件跑至关闭原会话、重开归档并核账。样例的有效原序是 14、20，手算得到 2 帧、总量 12、超限 1 帧。回归或演示断言失败时，命令返回非零退出码。
