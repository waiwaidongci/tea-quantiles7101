"""测试环境：在导入 src.config.database 之前提供占位环境变量。

数据库连接由各测试文件自建的引擎提供，这里的 URL 只用于 import 时不报错。
"""
import os

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')
