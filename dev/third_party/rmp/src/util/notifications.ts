import { createStandaloneToast } from '@chakra-ui/react';

export const { ToastContainer, toast } = createStandaloneToast();
export const sendErrorNotification = (title: string, description: string) => {
    toast({ title, description, status: 'error', duration: 9000, isClosable: true });
};
