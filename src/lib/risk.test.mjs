import test from 'node:test';
import assert from 'node:assert/strict';
import {riskPosition,lotRule} from './risk.ts';
const base={instrumentId:'XSHG:600900',equity:100000,cash:100000,entry:10,stop:9.5,current:10,riskPct:1,accountRiskPct:5,otherRisk:0,positionCapPct:30,existingQuantity:0,sellableQuantity:0,cost:10,commissionPct:0,minimumCommission:0,sellTaxPct:0,slippagePct:0};
test('risk budget example produces 2000 shares, and fees cannot increase size',()=>{
 const plain=riskPosition(base);assert.equal(plain.quantity,2000);assert.equal(plain.plannedLoss,1000);
 const fees=riskPosition({...base,commissionPct:.03,minimumCommission:5,sellTaxPct:.05,slippagePct:.1});assert.ok(fees.quantity<2000);assert.ok(fees.plannedLoss<=1000);assert.equal(fees.quantity%100,0);
});
test('cash, existing exposure and total account risk independently limit additions',()=>{
 assert.equal(riskPosition({...base,cash:999}).quantity,0);
 assert.equal(riskPosition({...base,otherRisk:5000}).quantity,0);
 const held=riskPosition({...base,existingQuantity:1500,sellableQuantity:1000});assert.equal(held.quantity,500);assert.equal(held.holding.locked,500);assert.ok(held.accountRisk<=5000);
 assert.equal(riskPosition({...base,existingQuantity:3000,sellableQuantity:3000}).quantity,0);
});
test('lot rules distinguish STAR, Beijing and regular stocks',()=>{
 assert.equal(lotRule('XSHG:688981').minimum,200);assert.equal(lotRule('XBSE:920002').step,1);
 assert.equal(riskPosition({...base,instrumentId:'XSHG:688981',riskPct:.099}).quantity,0);
 assert.equal(riskPosition({...base,instrumentId:'XBSE:920002',riskPct:.0755}).quantity,151);
});
test('invalid stops, nonfinite numbers and sellable holdings fail closed',()=>{
 for(const patch of [{stop:10},{equity:NaN},{cash:Infinity},{existingQuantity:100,sellableQuantity:101},{riskPct:-1}])assert.throws(()=>riskPosition({...base,...patch}));
});

test('holding-only review does not require or invent an additional buy',()=>{
 const held=riskPosition({...base,entry:0,existingQuantity:1000,sellableQuantity:600,current:11});
 assert.equal(held.quantity,0);assert.equal(held.holding.unrealized,1000);assert.equal(held.holding.locked,400);
 assert.equal(held.cashRequired,0);assert.equal(held.plannedLoss,0);assert.ok(held.existingRisk>0);
});
