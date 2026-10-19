import { pinyin } from 'pinyin-pro';

export type PinyinStationNameTranslationMode = 'pinyin-spaced' | 'pinyin-compact' | 'pinyin-uppercase';

const titleCaseWord = (word: string) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
const literalWordPattern = /^[A-Za-z0-9]$/;
const pinyinSyllableSeparatorPattern = /^[aoe]/i;
const pinyinOptions = { toneType: 'none', type: 'all', v: true } as const;

const translateStationNameLineBySpacedPinyin = (line: string): string => {
    let result = '';
    let previousLiteralWord = false;

    for (const token of pinyin(line, pinyinOptions)) {
        if (token.origin.trim().length === 0) {
            if (result.length > 0 && !result.endsWith(' ')) {
                result += ' ';
            }
            previousLiteralWord = false;
            continue;
        }

        const isPinyinWord = token.isZh;
        const isLiteralWord = !isPinyinWord && literalWordPattern.test(token.origin);
        const value = isPinyinWord ? titleCaseWord(token.result) : token.origin;

        if (isPinyinWord || isLiteralWord) {
            if (result.length === 0 || result.endsWith(' ')) {
                result += value;
            } else if (isLiteralWord && previousLiteralWord) {
                result += value;
            } else {
                result += ` ${value}`;
            }
        } else {
            result = `${result.trimEnd()}${value}`;
        }
        previousLiteralWord = isLiteralWord;
    }

    return result.trim();
};

const translateStationNameLineByCompactPinyin = (line: string): string => {
    let result = '';
    let shouldCapitalizeNextPinyin = true;
    let hasSeenPinyinWord = false;

    for (const token of pinyin(line, pinyinOptions)) {
        if (token.origin.trim().length === 0) {
            shouldCapitalizeNextPinyin = true;
            continue;
        }

        const isPinyinWord = token.isZh;
        if (isPinyinWord) {
            const value = token.result.toLowerCase();
            if (hasSeenPinyinWord && pinyinSyllableSeparatorPattern.test(value)) {
                result += "'";
            }
            result += shouldCapitalizeNextPinyin ? titleCaseWord(value) : value;
            shouldCapitalizeNextPinyin = false;
            hasSeenPinyinWord = true;
        } else {
            result += token.origin;
            shouldCapitalizeNextPinyin = true;
        }
    }

    return result.trim();
};

const translateStationNameLineByPinyin = (line: string, mode: PinyinStationNameTranslationMode): string => {
    switch (mode) {
        case 'pinyin-spaced':
            return translateStationNameLineBySpacedPinyin(line);
        case 'pinyin-compact':
            return translateStationNameLineByCompactPinyin(line);
        case 'pinyin-uppercase':
            return translateStationNameLineByCompactPinyin(line).toUpperCase();
    }
};

export const translateStationNameByPinyin = (
    zhName: string,
    mode: PinyinStationNameTranslationMode = 'pinyin-spaced'
): string => {
    return zhName
        .split('\n')
        .map(line => translateStationNameLineByPinyin(line, mode))
        .join('\n');
};
