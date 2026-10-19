export enum RmgEnv { DEV = 'DEV', UAT = 'UAT', PRD = 'PRD' }
export const logger = console;

export default {
    isStandaloneWindow: () => false,
    getColourMode: () => 'light',
    onColourModeChange: (callback: (mode: string) => void) => callback('light'),
};
