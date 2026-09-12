import assert from 'node:assert/strict';
import test from 'node:test';
import { orderSteps } from '../lib/steps.ts';

test('fan-out and fan-in are displayed after their prerequisites, not alphabetically', () => {
  const steps = [
    { id: 'combine', depends_on: ['left', 'right'] },
    { id: 'left', depends_on: ['root'] },
    { id: 'right', depends_on: ['root'] },
    { id: 'root', depends_on: [] },
  ];
  assert.deepEqual(orderSteps(steps).map(s => s.id), ['root', 'left', 'right', 'combine']);
  assert.equal(steps[0].id, 'combine');
});

test('malformed cyclic data remains inspectable instead of hanging the page', () => {
  const steps = [{ id: 'a', depends_on: ['b'] }, { id: 'b', depends_on: ['a'] }];
  assert.deepEqual(orderSteps(steps), steps);
});
