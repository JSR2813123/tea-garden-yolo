# 在 manage.py 同層的 __init__.py 加上這兩行來用 PyMySQL 當替代驅動
import pymysql
pymysql.install_as_MySQLdb()