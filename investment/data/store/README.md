# data/store

本地日线缓存目录（运行时写入，默认不入 git 大文件）。

## 结构

```
store/daily/    # 按标的缓存的 A/港/美日线
```

## 相关命令

```bash
python3 research/cache_cli.py --stats
```

由 `core/store.py` 与 `skills/common/history.py` 读写。

## 相关文档

- [数据层说明](../../docs/data-layer.md)
- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
