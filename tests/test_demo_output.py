"""Terminal results must be usable without parsing full solver JSON."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'examples'))
from demo_output import emit_result, server_path


class ReadableDemoOutput(unittest.TestCase):
    def data(self):
        return {'case':{'case_dir':'cases/input','data_kind':'synthetic_mgxs'},
                'parameter':{'unit':'macro'},
                'scan':{'scan_id':'a'*32,'state':'completed','cases':[
                    {'parameters':{'materials.1.density':.8},'state':'completed','job_id':'b'*32,
                     'keff':{'mean':1.6,'std_dev':None},'warnings':['统计量不可用，未替换为0']}]},
                'artifacts':{'workspace':'/server data','directory':'/server data/.mc-scans/'+'a'*32},
                'scan_plot':{'path':'.mc-scans/'+'a'*32+'/curve.png'},
                'reports':[{'format':'pdf','server_path':'/server data/report.pdf'}],
                'professional_acceptance':False}

    def render(self,data,machine=False):
        stream=io.StringIO()
        with redirect_stdout(stream):emit_result(data,SimpleNamespace(json=machine),'参数扫描执行结果')
        return stream.getvalue()

    def test_readable_summary_keeps_warnings_and_server_paths(self):
        text=self.render(self.data())
        self.assertIn('/server data/.mc-scans/'+'a'*32+'/curve.png',text)
        self.assertIn('标准差=不可用',text)
        self.assertIn('统计警告：统计量不可用，未替换为0',text)
        self.assertIn('宏观截面倍率',text)
        self.assertIn('未自动下载到客户端',text)
        self.assertNotIn('"cases": [',text)

    def test_json_preserves_complete_nested_payload(self):
        self.assertEqual(json.loads(self.render(self.data(),True)),self.data())

    def test_single_job_keff_and_unavailable_uncertainty_are_visible(self):
        text=self.render({'job':{'job_id':'a'*32,'state':'completed'},
                          'results':{'keff':{'mean':1.6,'std_dev':None},'warnings':['统计条件不足']}})
        self.assertIn('keff=1.6；标准差=不可用',text)
        self.assertIn('统计条件不足',text)

    def test_old_remote_service_never_invents_client_absolute_path(self):
        text=server_path({'path':'.mc-jobs/id/report.pdf'})
        self.assertIn('服务端相对路径',text)
        self.assertNotIn(str(Path.cwd()),text)

    def test_repair_rejection_does_not_claim_recalculation_success(self):
        text=self.render({'outcome':'not_applied','failed_job':{'job_id':'b'*32,'state':'failed'},
                          'case':{'case_dir':'cases/original','workspace':'/server'},
                          'prepared':{'case_dir':'cases/repaired'}})
        self.assertIn('未提交重算',text)
        self.assertIn('原失败编号',text)
        self.assertIn('修复副本（尚未应用修改）：/server/cases/repaired',text)

    def test_repair_summary_distinguishes_original_and_recalculation_input(self):
        text=self.render({'outcome':'recalculation_completed','case':{'case_dir':'cases/original','workspace':'/server'},
                          'prepared':{'case_dir':'cases/repaired'},'rerun':{'job_id':'b'*32,'state':'completed'}})
        self.assertIn('原输入（保留）：/server/cases/original',text)
        self.assertIn('修复副本（本次重算输入）：/server/cases/repaired',text)

    def test_given_server_absolute_path_is_preserved(self):
        self.assertEqual(server_path({'server_path':'/remote/actual.pdf','path':'other.pdf'},'/client'),' /remote/actual.pdf'.strip())
