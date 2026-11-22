export const cagesViewTemplate = `
<section class="panel">
  <h2>入笼冻结</h2>
  <p class="hint">从当事人可用保证金划入新笼，写入笼代。冻结后须履约核验，才能解笼或罚没。</p>
  <div class="stub-banner">
    超额命运（乙路径，2026-11-22 当日锁死）：可用额不足时正式笼零建、账户零改，
    只落拒入登记占住当日意图额度；同日再入须先撤销登记，否则继续拒绝并记超额偏航。
    详见 docs/入笼超额命运说明.md。
  </div>
  <div class="grid-2">
    <div>
      <div class="form-row">
        <div>
          <label>当事人键</label>
          <input v-model="freezeForm.party_key" list="party-list"/>
          <datalist id="party-list">
            <option v-for="b in bonds" :key="b.party_key" :value="b.party_key"/>
          </datalist>
        </div>
        <div>
          <label>合约号</label>
          <input v-model="freezeForm.contract_no"/>
        </div>
        <div>
          <label>金额</label>
          <input type="number" v-model.number="freezeForm.amount" min="1" step="1000"/>
        </div>
        <div>
          <label>受益人</label>
          <input v-model="freezeForm.beneficiary"/>
        </div>
        <div>
          <label>笼编码（可空自动）</label>
          <input v-model="freezeForm.cage_code" placeholder="CG-P-WEST-001"/>
        </div>
      </div>
      <div class="actions">
        <button @click="freezeCage">入笼冻结</button>
        <button class="secondary" @click="loadRejections(freezeForm.party_key)">查拒入登记</button>
        <button class="secondary" @click="refresh">刷新</button>
      </div>
      <h3>当日拒入登记 · {{ rejectionDay || '—' }}</h3>
      <table>
        <thead>
          <tr><th>#</th><th>当事人</th><th>金额</th><th>合约</th><th>状态</th><th>登记人</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in freezeRejections" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.party_key }}</td>
            <td>{{ fmt(r.amount) }}</td>
            <td>{{ r.contract_no }}</td>
            <td>
              <span class="badge" :class="r.status==='占额'?'fail':'done'">{{ r.status }}</span>
            </td>
            <td>{{ r.actor }}</td>
            <td>
              <button
                v-if="r.status==='占额'"
                class="mini-btn secondary"
                @click="revokeRejection(r)"
              >撤销</button>
              <span v-else>{{ r.revoked_by }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <div class="cage-cards">
        <div
          class="cage-card"
          v-for="c in cages"
          :key="c.id"
          :class="{selected: selectedCage && selectedCage.id===c.id}"
          @click="selectCage(c)"
        >
          <div class="stamp-corner">{{ shortStatus(c.status) }}</div>
          <div class="code">{{ c.cage_code }}</div>
          <div class="amt">{{ fmt(c.available) }} / {{ fmt(c.amount) }}</div>
          <div>{{ c.contract_no }} · 代{{ c.generation }}</div>
          <div>{{ c.party_key }} → {{ c.beneficiary }}</div>
        </div>
      </div>
    </div>
  </div>
  <div v-if="cageDetail">
    <h3>笼详情 · {{ cageDetail.cage.cage_code }}</h3>
    <table>
      <thead><tr><th>字段</th><th>值</th></tr></thead>
      <tbody>
        <tr><td>状态</td><td><span class="badge" :class="statusClass(cageDetail.cage.status)">{{ cageDetail.cage.status }}</span></td></tr>
        <tr><td>可用 / 总额</td><td>{{ fmt(cageDetail.cage.available) }} / {{ fmt(cageDetail.cage.amount) }}</td></tr>
        <tr><td>核验条数</td><td>{{ cageDetail.attestations.length }}</td></tr>
        <tr><td>拨付条数</td><td>{{ cageDetail.releases.length }}</td></tr>
        <tr><td>罚没条数</td><td>{{ cageDetail.forfeits.length }}</td></tr>
      </tbody>
    </table>
  </div>
</section>
`;
