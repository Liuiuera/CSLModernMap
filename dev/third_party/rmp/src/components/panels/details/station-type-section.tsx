import { Box, Heading } from '@chakra-ui/react';
import { useTranslation } from 'react-i18next';
import { StationType } from '../../../constants/stations';
import { useRootDispatch, useRootSelector } from '../../../redux';
import { saveGraph } from '../../../redux/param/param-slice';
import { refreshNodesThunk } from '../../../redux/runtime/runtime-slice';
import { changeStationType } from '../../../util/change-types';
import { confirmStationTypeChange } from '../../../util/station-style-change';
import StationStylePicker from './station-style-picker';

export default function StationTypeSection() {
    const { t } = useTranslation();
    const dispatch = useRootDispatch();
    const { selected } = useRootSelector(state => state.runtime);
    useRootSelector(state => state.runtime.refresh.nodes);
    const populateTransfer = useRootSelector(state => state.app.preference.autoChangeStationType);
    const [id] = selected;
    const type = window.graph.getNodeAttribute(id, 'type') as StationType;
    return (
        <Box>
            <Heading as="h5" size="sm">
                {t('cmm.stationStyle')}
            </Heading>
            <StationStylePicker
                value={type}
                onChange={next => {
                    if (next === type || !confirmStationTypeChange([id], next)) return;
                    changeStationType(window.graph, id, next, populateTransfer);
                    dispatch(saveGraph(window.graph.export()));
                    dispatch(refreshNodesThunk());
                }}
            />
        </Box>
    );
}
