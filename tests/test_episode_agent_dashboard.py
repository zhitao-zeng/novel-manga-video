"""The board renders the same saved Agent stages and resource counts as the CLI."""
from pathlib import Path
import subprocess


def test_agent_stages_pool_and_unresolved_result_are_visible_without_false_counts():
    source = Path(__file__).resolve().parents[1] / 'src/novel_manga/dashboard/static/pipeline.js'
    code = r'''const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const row={executor:'episode_agent',status:'running',controller_alive:true,in_flight:2,pending:3,blocked:1,updated_at:'10:15',
  pools:{h3:{held:2,limit:6}},agents:{
    2:{phase:'waiting_resource',status:'working',waiting_for:'agent_model'},
    3:{phase:'tool_running',status:'working',job:{action:'review',result:{generation_counts:{a:1,b:2}}}},
    4:{phase:'finished',status:'finished_with_issues',reason:'<failed still unresolved>'}}};
const html=c.pipelineFlowStatus({flows:{production:row}});
for(const text of ['等待模型资源','独立审片','已收尾，仍有问题','2 / 6','10:15','&lt;failed still unresolved&gt;','Agent 各集阶段'])
  if(!html.includes(text))throw Error(text+' absent: '+html);
if(html.includes('undefined')||html.includes('NaN')||html.includes('<failed still unresolved>'))throw Error(html);
if(c.pipelineAgentStatus({executor:'standard',agents:row.agents})!=='')throw Error('Agent status leaked into standard');
'''
    subprocess.run(['node', '-e', code, str(source)], check=True)
