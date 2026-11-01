import React from 'react';
import { LuChevronDown } from 'react-icons/lu';
import {
    Button,
    Image,
    Popover,
    PopoverBody,
    PopoverContent,
    PopoverTrigger,
    Portal,
    SimpleGrid,
    Text,
} from '@chakra-ui/react';
import { useTranslation } from 'react-i18next';
import { StationType } from '../../../constants/stations';
import { stationCatalog } from '../../../util/station-catalog';

export default function StationStylePicker({
    value,
    onChange,
}: {
    value?: StationType;
    onChange: (type: StationType) => void;
}) {
    const { t } = useTranslation();
    const portalContainer = React.useRef(document.getElementById('root'));
    const catalog = stationCatalog();
    const current = catalog.find(item => item.type === value);
    const initialChoice = React.useRef<HTMLButtonElement>(null);
    const trigger = React.useRef<HTMLButtonElement>(null);
    const [focusIndex, setFocusIndex] = React.useState(0);
    return (
        <Popover
            placement="left-start"
            strategy="fixed"
            isLazy
            closeOnBlur
            returnFocusOnClose
            initialFocusRef={initialChoice}
            onOpen={() =>
                setFocusIndex(
                    Math.max(
                        0,
                        catalog.findIndex(item => item.type === value)
                    )
                )
            }
            modifiers={[{ name: 'preventOverflow', options: { padding: 12 } }]}
        >
            {({ onClose }) => (
                <>
                    <PopoverTrigger>
                        <Button
                            ref={trigger}
                            variant="outline"
                            aria-label={`${t('cmm.chooseStyle')}: ${current?.label ?? ''}`}
                            width="100%"
                            height="auto"
                            minHeight="36px"
                            justifyContent="space-between"
                        >
                            {current && (
                                <Image src={current.preview} alt="" width="68px" height="40px" objectFit="contain" />
                            )}
                            {current?.label ?? t('cmm.chooseStyle')}
                            <LuChevronDown aria-hidden="true" />
                        </Button>
                    </PopoverTrigger>
                    <Portal containerRef={portalContainer}>
                        <PopoverContent
                            className="cmm-style-popover"
                            aria-label={t('cmm.chooseStyle')}
                            width="min(396px, calc(100vw - 24px))"
                            maxHeight="calc(100dvh - 24px)"
                            onKeyDown={event => {
                                if (event.key !== 'Escape') return;
                                event.preventDefault();
                                event.stopPropagation();
                                onClose();
                                trigger.current?.focus({ preventScroll: true });
                            }}
                        >
                            <PopoverBody maxHeight="min(420px, calc(100dvh - 32px))" overflowY="auto">
                                <SimpleGrid
                                    columns={3}
                                    gap={1}
                                    aria-label={t('cmm.chooseStyle')}
                                    onKeyDown={event => {
                                        const buttons = [
                                            ...event.currentTarget.querySelectorAll<HTMLButtonElement>('button'),
                                        ];
                                        const index = buttons.indexOf(event.target as HTMLButtonElement);
                                        const next = {
                                            ArrowLeft: index - 1,
                                            ArrowRight: index + 1,
                                            ArrowUp: index - 3,
                                            ArrowDown: index + 3,
                                            Home: 0,
                                            End: buttons.length - 1,
                                        }[event.key];
                                        if (next === undefined || index < 0) return;
                                        event.preventDefault();
                                        const target = Math.max(0, Math.min(next, buttons.length - 1));
                                        setFocusIndex(target);
                                        buttons[target].focus();
                                        buttons[target].scrollIntoView({ block: 'nearest' });
                                    }}
                                >
                                    {catalog.map((item, index) => (
                                        <Button
                                            key={item.type}
                                            ref={
                                                item.type === value || (!value && index === 0)
                                                    ? initialChoice
                                                    : undefined
                                            }
                                            tabIndex={index === focusIndex ? 0 : -1}
                                            onFocus={() => setFocusIndex(index)}
                                            variant="outline"
                                            height="78px"
                                            p={1}
                                            flexDirection="column"
                                            aria-pressed={item.type === value}
                                            onClick={() => {
                                                onChange(item.type);
                                                onClose();
                                                trigger.current?.focus({ preventScroll: true });
                                            }}
                                        >
                                            <Image
                                                src={item.preview}
                                                alt=""
                                                width="108px"
                                                height="48px"
                                                objectFit="contain"
                                            />
                                            <Text fontSize="sm">{item.label}</Text>
                                        </Button>
                                    ))}
                                </SimpleGrid>
                            </PopoverBody>
                        </PopoverContent>
                    </Portal>
                </>
            )}
        </Popover>
    );
}
