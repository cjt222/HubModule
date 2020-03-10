import os
import numpy as np
import paddlehub as hub
import paddle.fluid as fluid
from resnet.resnet import ResNet, ResNetC5
from paddlehub.module.module import moduleinfo
from resnet.processor import load_label_info
from resnet.data_feed import test_reader


@moduleinfo(
    name="resnet",
    version="2.0.0",
    type="cv/image_classification",
    summary="for test",
    author="paddle",
    author_email="paddlepaddle@baidu.com")
class _ResNet(hub.Module):
    def _initialize(self):
        self.default_pretrained_model_path = os.path.join(
            self.directory, "ResNet34_vd_pretrained")
        self.label_names = load_label_info(
            os.path.join(self.directory, "label_file.txt"))
        self.infer_prog = None
        self.pred_out = None

    def context(self,
                input_image=None,
                trainable=True,
                pretrained=False,
                param_prefix='',
                get_prediction=False,
                depth=34,
                variant='d',
                norm_type='bn',
                feature_maps=[3, 4, 5],
                return_c5=False):
        """Distill the Head Features, so as to perform transfer learning.

        :param input_image: image tensor.
        :type input_image: <class 'paddle.fluid.framework.Variable'>
        :param trainable: whether to set parameters trainable.
        :type trainable: bool
        :param pretrained: whether to load default pretrained model.
        :type pretrained: bool
        :param param_prefix: the prefix of parameters in yolo_head and backbone
        :type param_prefix: str
        :param get_prediction: whether to get prediction,
            if True, outputs is {'bbox_out': bbox_out},
            if False, outputs is {'head_features': head_features}.
        :type get_prediction: bool
        :param depth: depth of network
        :type depth: int
        :param variant: type of resnet
        :type variant: str
        :param norm_type: type of normlization
        :type norm_type: str
        :param feature_maps: stage of output
        :type feature_maps: list
        """
        context_prog = input_image.block.program if input_image else fluid.Program(
        )
        with fluid.program_guard(context_prog):
            if return_c5:
                return ResNetC5(
                    depth=depth,
                    norm_type=norm_type,
                    variant=variant,
                    feature_maps=feature_maps)
            image = input_image if input_image else fluid.data(
                name='image',
                shape=[-1, 3, 224, 224],
                dtype='float32',
                lod_level=0)
            backbone = ResNet(depth=depth, variant=variant, norm_type=norm_type,\
                              feature_maps=feature_maps, get_prediction=get_prediction)

            out = backbone(image)
            inputs = {'image': image}
            if get_prediction:
                outputs = {'pred_out': out}
            else:
                outputs = {'body_feats': out}

        place = fluid.CPUPlace()
        exe = fluid.Executor(place)
        with fluid.program_guard(context_prog):
            if pretrained:

                def _if_exist(var):
                    return os.path.exists(
                        os.path.join(self.default_pretrained_model_path,
                                     var.name))

                if not param_prefix:
                    fluid.io.load_vars(
                        exe,
                        self.default_pretrained_model_path,
                        main_program=context_prog,
                        predicate=_if_exist)

            return inputs, outputs, context_prog

    def classification(self,
                       paths=None,
                       images=None,
                       use_gpu=False,
                       batch_size=1,
                       output_dir=None,
                       score_thresh=0.5):
        """API of Classification.
        :param paths: the path of images.
        :type paths: list, each element is correspond to the path of an image.
        :param images: data of images, [N, H, W, C]
        :type images: numpy.ndarray
        :param use_gpu: whether to use gpu or not.
        :type use_gpu: bool
        :param batch_size: bathc size.
        :type batch_size: int
        :param output_dir: the directory to store the detection result.
        :type output_dir: str
        :param score_thresh: the threshold of detection confidence.
        :type score_thresh: float
        """
        if self.infer_prog is None:
            inputs, outputs, self.infer_prog = self.context(
                trainable=False, pretrained=True, get_prediction=True)
            self.infer_prog = self.infer_prog.clone(for_test=True)
            self.pred_out = outputs['pred_out']
        place = fluid.CUDAPlace(0) if use_gpu else fluid.CPUPlace()
        exe = fluid.Executor(place)
        all_images = []
        paths = paths if paths else []
        for yield_data in test_reader(paths, images):
            all_images.append(yield_data)

        images_num = len(all_images)
        loop_num = int(np.ceil(images_num / batch_size))

        class_maps = load_label_info("./label_file.txt")
        res_list = []
        TOPK = 1
        for iter_id in range(loop_num):
            batch_data = []
            handle_id = iter_id * batch_size
            for image_id in range(batch_size):
                try:
                    batch_data.append(all_images[handle_id + image_id])
                except:
                    pass
            feed = {'image': np.array(batch_data).astype('float32')}
            result = exe.run(
                self.infer_prog,
                feed=feed,
                fetch_list=[self.pred_out],
                return_numpy=True)
            for i, res in enumerate(result[0]):
                pred_label = np.argsort(res)[::-1][:TOPK]
                class_name = class_maps[int(pred_label)]
                res_list.append([pred_label, class_name])
        return res_list
