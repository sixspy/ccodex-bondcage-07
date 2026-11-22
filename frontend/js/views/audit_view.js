export const auditViewTemplate = `
<section class="panel">
  <h2>审计争用</h2>
  <p class="hint">写动作落审计；后到争用只记 contention，不写业务。拒入登记与账户/笼面同源。</p>
  <div class="stub-banner">
    五页同源核对：账户冻结 {{ fmt((recon.parties||[]).reduce((s,p)=>s+p.frozen,0)) }} ·
    活笼可用 {{ fmt((recon.parties||[]).reduce((s,p)=>s+p.live_cage_available,0)) }} ·
    当日拒入占额 {{ fmt((recon.parties||[]).reduce((s,p)=>s+p.rejected_total,0)) }} ·
    账户超额当事人 {{ (recon.summary||{}).over_limit_parties || 0 }}
  </div>
  <div class="grid-2">
    <div>
      <h3>审计</h3>
      <table>
        <thead><tr><th>时间</th><th>人</th><th>动作</th><th>目标</th><th>载荷</th></tr></thead>
        <tbody>
          <tr v-for="a in audits" :key="a.id">
            <td>{{ a.created_at }}</td>
            <td>{{ a.actor }}</td>
            <td>
              <span
                class="badge"
                :class="a.action==='cage_freeze_deviation'?'fail':(a.action==='cage_freeze_rejected'?'done':'frozen')"
              >{{ a.action }}</span>
            </td>
            <td>{{ a.target }}</td>
            <td><code style="font-size:11px">{{ a.payload }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>当日拒入登记</h3>
      <p class="hint">占额=仍占当日意图额度；已撤销后才可同日再入。</p>
      <table>
        <thead><tr><th>#</th><th>当事人</th><th>金额</th><th>状态</th><th>登记人</th></tr></thead>
        <tbody>
          <tr v-for="r in freezeRejections" :key="'r'+r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.party_key }}</td>
            <td>{{ fmt(r.amount) }}</td>
            <td><span class="badge" :class="r.status==='占额'?'fail':'done'">{{ r.status }}</span></td>
            <td>{{ r.actor }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">持锁</h3>
      <table>
        <thead><tr><th>键</th><th>持有者</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="l in locks" :key="l.lock_key">
            <td>{{ l.lock_key }}</td>
            <td>{{ l.holder }}</td>
            <td>{{ l.acquired_at }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">争用</h3>
      <table>
        <thead><tr><th>时间</th><th>键</th><th>人</th><th>动作</th><th>详情</th></tr></thead>
        <tbody>
          <tr v-for="c in contention" :key="c.id">
            <td>{{ c.created_at }}</td>
            <td>{{ c.lock_key }}</td>
            <td>{{ c.actor }}</td>
            <td>{{ c.action }}</td>
            <td>{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
