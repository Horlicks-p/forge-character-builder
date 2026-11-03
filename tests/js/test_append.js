// Run: node tests/js/test_append.js
const assert = require("assert");
const { computeCharacterBuilderAppend: append } = require("../../javascript/character_builder.js");

const cases = [
    // [current, fragment, lastAppended, expectedText, expectedStatus]
    ["", "black hair, long hair", undefined, "black hair, long hair", "Prompt appended"],
    ["1girl, <lora:char:0.8>", "black hair", undefined, "1girl, <lora:char:0.8>, black hair", "Prompt appended"],
    ["1girl, (smile:1.2) BREAK", "black hair", undefined, "1girl, (smile:1.2) BREAK, black hair", "Prompt appended"],
    ["1girl,", "black hair", undefined, "1girl, black hair", "Prompt appended"],
    ["1girl,  \n", "black hair", undefined, "1girl, black hair", "Prompt appended"],
    ["1girl, black hair", "black hair", "black hair", "1girl, black hair", "Already appended"],
    ["1girl, black hair, smile", "black hair", "black hair", "1girl, black hair, smile, black hair", "Prompt appended"],
    ["1girl, black hair", "black hair", undefined, "1girl, black hair, black hair", "Prompt appended"],
    ["1girl", "   ", undefined, "1girl", "Nothing to append"],
];
for (const [current, fragment, last, text, status] of cases) {
    const result = append(current, fragment, last);
    assert.strictEqual(result.text, text, `text for ${JSON.stringify([current, fragment, last])}`);
    assert.strictEqual(result.status, status, `status for ${JSON.stringify([current, fragment, last])}`);
}
console.log(`ok - ${cases.length} append cases`);
